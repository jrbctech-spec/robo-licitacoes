"""
edital_parser.py – Leitor e extrator avançado de editais de licitação.

Recursos:
  1. Isolamento automático da seção 'Termo de Referência' / 'Anexo I' / 'Quadro de Itens'.
  2. Filtragem agressiva de cláusulas jurídicas/administrativas.
  3. Extração multi-estratégia:
     a) Detecção de tabela estruturada (Grupo|Item|Descrição|...|Valor)
     b) Linhas com prefixo "ITEM N – descrição" com valor R$ próximo
     c) Gemini AI fallback (Structured JSON) se RegEx falhar
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pypdf import PdfReader

from config import settings

logger = logging.getLogger(__name__)


# ─── Modelos de Inteligência Regulatória Compras.gov.br ─────────────────────────

class SeveridadeRisco(str, Enum):
    """Nível de criticidade de um risco de desclassificação ou inabilitação."""
    ALTO = "ALTO"       # Desclassificação imediata / Sumária
    MEDIO = "MEDIO"     # Risco de desclassificação se não justificado / Risco operacional
    BAIXO = "BAIXO"     # Requisito formal / Documental sanável
    INFO = "INFO"       # Informação e boa prática de conformidade


class CriterioJulgamento(str, Enum):
    """Critérios de julgamento conforme art. 33 da Lei 14.133/2021 e Compras.gov.br."""
    MENOR_PRECO_ITEM = "menor_preco_item"
    MENOR_PRECO_GRUPO = "menor_preco_grupo_lote"
    MENOR_PRECO_GLOBAL = "menor_preco_global"
    MAIOR_DESCONTO = "maior_desconto"
    MELHOR_TECNICA_PRECO = "melhor_tecnica_preco"
    MAIOR_LANCE = "maior_lance"


class TipoModoDisputa(str, Enum):
    """Modos de disputa operacionais suportados pelo Compras.gov.br (IN 73/2022)."""
    ABERTO = "aberto"
    ABERTO_FECHADO = "aberto_fechado"
    FECHADO_ABERTO = "fechado_aberto"
    DISPENSA_ELETRONICA = "dispensa_eletronica"


@dataclass
class ModoDisputaDetalhado:
    """Regras operacionais do modo de disputa segundo a IN 73/2022 e Lei 14.133/2021."""
    tipo: TipoModoDisputa = TipoModoDisputa.ABERTO
    nome_legivel: str = "Aberto"
    tempo_etapa_aberta_minutos: int = 10
    tempo_prorrogacao_minutos: int = 2
    tempo_etapa_fechada_minutos: int = 0
    percentual_etapa_fechada: float = 0.0      # ex: 10.0%
    max_propostas_etapa_fechada: int = 3
    descricao_operacional: str = (
        "Etapa aberta de 10 min. Prorroga-se automaticamente por mais 2 min "
        "sempre que houver lance nos últimos 2 minutos."
    )


@dataclass
class IntervaloLances:
    """Regras de degrau e intervalo entre lances no Compras.gov.br."""
    decremento_minimo_reais: Optional[float] = None
    decremento_minimo_percentual: Optional[float] = None
    intervalo_tempo_minimo_segundos: int = 3   # 3 segundos padrão Compras.gov contra flood
    exige_decremento_proprio: bool = True     # Lances intermediários devem superar o próprio lance anterior
    descricao: str = "Decremento padrão livre respeitando R$ 0,01."


@dataclass
class CronogramaCertame:
    """Horários e cronograma oficial da sessão pública (Horário de Brasília)."""
    data_abertura: Optional[str] = None       # ex: "15/10/2026"
    hora_abertura: Optional[str] = None       # ex: "09:00"
    fuso_horario: str = "Brasília (UTC-3)"
    prazo_envio_proposta_ajustada_horas: int = 2 # mínimo regulamentar de 2 horas (art. 56 da IN 73/2022)
    fase_atual: str = "Aguardando Sessão"
    data_limite_impugnacao: Optional[str] = None


@dataclass
class RiscoDesclassificacao:
    """Identifica um risco concreto de desclassificação ou inabilitação no certame."""
    id_risco: str
    titulo: str
    severidade: SeveridadeRisco
    artigo_legal: str                         # ex: "Art. 59, III da Lei 14.133/2021"
    descricao: str
    como_evitar: str

    @property
    def base_legal(self) -> str:
        return self.artigo_legal

    @property
    def sugestao_mitigacao(self) -> str:
        return self.como_evitar



@dataclass
class InteligenciaComprasGov:
    """Cruzamento completo das regras do Aviso/TR com a regulamentação do Compras.gov.br."""
    criterio_julgamento: CriterioJulgamento = CriterioJulgamento.MENOR_PRECO_ITEM
    criterio_julgamento_legivel: str = "Menor Preço por Item"
    modo_disputa: ModoDisputaDetalhado = field(default_factory=ModoDisputaDetalhado)
    intervalo_lances: IntervaloLances = field(default_factory=IntervaloLances)
    cronograma: CronogramaCertame = field(default_factory=CronogramaCertame)
    limite_inexequibilidade_percent: float = 50.0  # Presunção de inexequibilidade (art. 59 §4º Lei 14.133)
    permite_lance_igual: bool = False
    vedado_valor_superior_referencia: bool = True
    riscos_identificados: list[RiscoDesclassificacao] = field(default_factory=list)


# ─── Modelos de Dados do Edital ────────────────────────────────────────────────

@dataclass
class ItemEdital:
    """Representa um item/lote encontrado no edital."""
    numero_item: int
    descricao: str
    unidade_medida: str = "UN"
    quantidade: float = 1.0
    valor_referencia: Optional[float] = None
    decremento_minimo: Optional[float] = None
    decremento_percentual: Optional[float] = None
    tipo_item: str = "produto"   # "servico" ou "produto"
    piso_preco: Optional[float] = None
    custo_real: Optional[float] = None


@dataclass
class RegraEdital:
    """Regras globais extraídas do edital."""
    modo_disputa: str = "aberto"
    decremento_global_valor: Optional[float] = None
    decremento_global_percent: Optional[float] = None
    piso_global: Optional[float] = None
    criterio_julgamento: str = "menor_preco"
    permite_consorcio: bool = False
    exclusivo_me_epp: bool = False
    texto_raw: str = field(default="", repr=False)


@dataclass
class ResultadoEdital:
    """Resultado completo do parsing de um edital com Inteligência Regulatória."""
    caminho_pdf: str
    numero_pregao: str = ""
    numero_uasg: str = ""
    orgao: str = ""
    objeto: str = ""
    regras: RegraEdital = field(default_factory=RegraEdital)
    inteligencia_compras_gov: InteligenciaComprasGov = field(default_factory=InteligenciaComprasGov)
    itens: list[ItemEdital] = field(default_factory=list)
    erros_parsing: list[str] = field(default_factory=list)
    sucesso: bool = False
    extracao_ia_usada: bool = False


class _Padroes:
    """Padrões compilados para extração de texto de editais e regras do Compras.gov."""
    NUMERO_PREGAO = re.compile(r"(?:preg[ãa]o\s+(?:eletr[oô]nico\s+)?|dispensa\s+eletr[oô]nica\s+|concorr[êe]ncia\s+eletr[oô]nica\s+)n[º°]?\s*(\d+/\d{4})", re.IGNORECASE)
    NUMERO_UASG = re.compile(r"(?:uasg|unidade\s+gestora(?:\s+compradora)?|c[óo]digo\s+da\s+uasg)\s*[:\-–]?\s*(\d{5,6})", re.IGNORECASE)
    OBJETO = re.compile(r"objeto[:\s]+([^\n]{20,300})", re.IGNORECASE)

    # Modos de disputa
    MODO_ABERTO_FECHADO = re.compile(r"modo(?:\s+de\s+disputa)?\s*[:\-–]?\s*(?:aberto\s+e\s+fechado|aberto/fechado)", re.IGNORECASE)
    MODO_FECHADO_ABERTO = re.compile(r"modo(?:\s+de\s+disputa)?\s*[:\-–]?\s*(?:fechado\s+e\s+aberto|fechado/aberto)", re.IGNORECASE)
    MODO_ABERTO = re.compile(r"modo(?:\s+de\s+disputa)?\s*[:\-–]?\s*aberto\b", re.IGNORECASE)
    MODO_FECHADO = re.compile(r"modo(?:\s+de\s+disputa)?\s*[:\-–]?\s*fechado\b", re.IGNORECASE)
    MODO_DISPENSA = re.compile(r"dispensa\s+eletr[oô]nica", re.IGNORECASE)

    # Critérios de julgamento
    CRIT_MENOR_PRECO_GRUPO = re.compile(r"(?:crit[eé]rio\s+de\s+julgamento|julgamento|tipo\s+de\s+licita[çc][ãa]o)[\s\S]{0,50}?menor\s+pre[çc]o[\s\S]{0,30}?(?:por\s+grupo|por\s+lote|global\s+por\s+lote|grupo\s*/\s*lote|lote|grupo)", re.IGNORECASE)
    CRIT_MENOR_PRECO_GLOBAL = re.compile(r"(?:crit[eé]rio\s+de\s+julgamento|julgamento|tipo\s+de\s+licita[çc][ãa]o)[\s\S]{0,50}?menor\s+pre[çc]o[\s\S]{0,30}?(?:global|total)", re.IGNORECASE)
    CRIT_MENOR_PRECO_ITEM = re.compile(r"(?:crit[eé]rio\s+de\s+julgamento|julgamento|tipo\s+de\s+licita[çc][ãa]o)[\s\S]{0,50}?menor\s+pre[çc]o[\s\S]{0,30}?(?:por\s+item|unit[áa]rio|item)", re.IGNORECASE)
    CRIT_MAIOR_DESCONTO = re.compile(r"(?:crit[eé]rio\s+de\s+julgamento|julgamento|tipo\s+de\s+licita[çc][ãa]o)[\s\S]{0,50}?maior\s+desconto", re.IGNORECASE)


    # Intervalos / Degraus de lances
    DECREMENTO_VALOR = re.compile(r"(?:decremento|intervalo|degrau|diferen[çc]a)\s+m[íi]nimo?[\s\S]{0,60}?R\$\s*([\d.,]+)", re.IGNORECASE)
    DECREMENTO_PERCENT = re.compile(r"(?:decremento|intervalo|degrau|diferen[çc]a)\s+m[íi]nimo?[\s\S]{0,60}?([\d.,]+)\s*%", re.IGNORECASE)

    # Horários e Sessão Pública
    DATA_HORA_ABERTURA = re.compile(
        r"(?:abertura\s+da\s+sess[ãa]o|in[íi]cio\s+da\s+sess[ãa]o|sess[ãa]o\s+p[úu]blica|disputa\s+de\s+lances)[\s\S]{0,60}?"
        r"(\d{1,2}/\d{1,2}/\d{4})[\s\S]{0,30}?(\d{1,2}[:h]\d{2})",
        re.IGNORECASE,
    )
    HORA_BRASILIA = re.compile(r"(\d{1,2}[:h]\d{2})\s*(?:horas?)?\s*(?:\(?(?:hor[áa]rio\s+de\s+bras[íi]lia|hor[áa]rio\s+oficial\s+de\s+bras[íi]lia)\)?)", re.IGNORECASE)

    # Riscos de Desclassificação / Conformidade
    VEDACAO_VALOR_MAXIMO = re.compile(r"(?:desclassificad[ao]s?|n[ãa]o\s+ser[ãa]o\s+aceit[ao]s?).*(?:acima|superior)\s+ao\s+(?:valor\s+m[áa]ximo|valor\s+estimado|pre[çc]o\s+de\s+refer[êe]ncia|or[çc]amento)", re.IGNORECASE)
    INEXEQUIBILIDADE = re.compile(r"(?:inexequ[ií]ve[li]s?|inexequibilidade|art(?:igo)?\.?\s*59|50%\s*(?:cinquenta\s+por\s+cento)?\s*do\s+valor)", re.IGNORECASE)
    PRAZO_PROPOSTA_AJUSTADA = re.compile(r"(?:proposta\s+adequada|proposta\s+ajustada|proposta\s+readequada|envio\s+da\s+proposta\s+vencedora).*?(\d{1,2}|duas|tr[êe]s|quatro)\s*horas?", re.IGNORECASE)
    DECLARACAO_ME_EPP = re.compile(r"(?:enquadramento|tratamento\s+favorecido|lei\s+complementar\s+123|me/epp)", re.IGNORECASE)
    CATMAT_CATSER = re.compile(r"(?:c[óo]digo\s+catmat|c[óo]digo\s+catser|catmat|catser)", re.IGNORECASE)

    QUANTIDADE_UNIDADE = re.compile(
        r"([\d.,]+)\s*(un(?:id(?:ade)?)?|und|kg|g\b|mg|ml|litro|l(?:t)?|cx|caixa|pç|peça|pe[cç]a"
        r"|servi[çc]o|hora|h\b|m[eê]s|pct|pacote|gl|gal[ãa]o|par|rl|rolo|fr|frasco"
        r"|amp|ampola|comp|comprimido|tb|tubo|fl|folha|resma|ct|cartucho"
        r"|ton|tonelada|saco|sc|bd|bobina|metro|m\b|turma)\b",
        re.IGNORECASE,
    )

    VALOR_REAIS = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)


_BLACKLIST = [
    "termo de referência",
    "anexo i",
    "disposições gerais",
    "condições de participação",
    "habilitação jurídica",
    "qualificação técnica",
    "regularidade fiscal",
    "critério de julgamento",
    "recursos administrativos",
    "penalidades",
    "sanções",
    "obrigações da contratada",
    "obrigações da contratante",
    "fiscalização",
    "pagamento",
    "reajuste",
    "garantia",
    "vigência",
    "foro",
    "compras.gov.br",
    "portal de compras",
    "impugnação",
    "esclarecimento",
    "pregoeiro",
    "comissão de contratação",
]

_VERBOS_ADMIN = re.compile(
    r"\b(?:dever[áa]|dever[ãa]o|poder[áa]|poder[ãa]o|ser[áa]|ser[ãa]o|obriga-se|fica\s+vedad[ao]|"
    r"caber[áa]|caber[ãa]o|compromete-se|apresentar|comprovar|encaminhar)\b",
    re.IGNORECASE,
)


class EditalParser:
    """Extrai regras e itens de editais de pregão eletrônico em formato PDF."""

    def __init__(self) -> None:
        self._padroes = _Padroes()

    def parsear(self, caminho_pdf: str | Path) -> ResultadoEdital:
        caminho = Path(caminho_pdf)
        resultado = ResultadoEdital(caminho_pdf=str(caminho))

        if not caminho.exists():
            resultado.erros_parsing.append(f"Arquivo não encontrado: {caminho}")
            return resultado

        if not caminho.suffix.lower() == ".pdf":
            resultado.erros_parsing.append(f"Arquivo não é um PDF: {caminho.suffix}")
            return resultado

        try:
            texto_completo = self._extrair_texto_pdf(caminho)
        except Exception as exc:
            resultado.erros_parsing.append(f"Falha ao ler PDF: {exc}")
            logger.error(f"Erro ao extrair texto do PDF {caminho}: {exc}")
            return resultado

        logger.info(f"PDF lido: {caminho.name} ({len(texto_completo)} caracteres)")
        resultado.regras.texto_raw = texto_completo

        self._extrair_cabecalho(texto_completo, resultado)
        self._extrair_regras(texto_completo, resultado)
        self._cruzar_regras_compras_gov(texto_completo, resultado)

        # Extração de itens (multi-estratégia + Gemini AI multimodal fallback)
        self._extrair_itens(texto_completo, resultado, caminho)

        resultado.sucesso = len(resultado.itens) > 0 and len(resultado.erros_parsing) == 0
        return resultado

    @staticmethod
    def _extrair_texto_pdf(caminho: Path) -> str:
        reader = PdfReader(str(caminho))
        paginas: list[str] = []
        for i, pagina in enumerate(reader.pages):
            try:
                texto = pagina.extract_text() or ""
                paginas.append(texto)
            except Exception as exc:
                logger.warning(f"Falha ao extrair página {i + 1}: {exc}")
        return "\n".join(paginas)

    def _extrair_cabecalho(self, texto: str, resultado: ResultadoEdital) -> None:
        m = self._padroes.NUMERO_PREGAO.search(texto)
        if m:
            resultado.numero_pregao = m.group(1).strip()

        m = self._padroes.NUMERO_UASG.search(texto)
        if m:
            resultado.numero_uasg = m.group(1).strip()

        m = self._padroes.OBJETO.search(texto)
        if m:
            resultado.objeto = re.sub(r"\s+", " ", m.group(1).strip())[:500]

    def _extrair_regras(self, texto: str, resultado: ResultadoEdital) -> None:
        regras = resultado.regras
        if self._padroes.MODO_ABERTO_FECHADO.search(texto):
            regras.modo_disputa = "aberto_fechado"
        elif self._padroes.MODO_FECHADO_ABERTO.search(texto):
            regras.modo_disputa = "fechado_aberto"
        elif self._padroes.MODO_FECHADO.search(texto):
            regras.modo_disputa = "fechado"
        elif self._padroes.MODO_DISPENSA.search(texto):
            regras.modo_disputa = "dispensa_eletronica"
        elif self._padroes.MODO_ABERTO.search(texto):
            regras.modo_disputa = "aberto"

        m = self._padroes.DECREMENTO_PERCENT.search(texto)
        if m:
            regras.decremento_global_percent = self._str_para_float(m.group(1))

        if regras.decremento_global_percent is None:
            m = self._padroes.DECREMENTO_VALOR.search(texto)
            if m:
                regras.decremento_global_valor = self._str_para_float(m.group(1))

    def _cruzar_regras_compras_gov(self, texto: str, resultado: ResultadoEdital) -> None:
        """
        Cruza o Aviso de Contratação / Termo de Referência com a regulamentação do Compras.gov.br:
          - IN SEGES/ME nº 73/2022 (Pregão e Concorrência Eletrônica na Lei 14.133/2021)
          - IN SEGES/ME nº 67/2021 (Dispensa Eletrônica)
          - Decreto nº 10.024/2019
        """
        intel = resultado.inteligencia_compras_gov

        # ── 1. Critério de Julgamento ─────────────────────────────────────────
        if self._padroes.CRIT_MAIOR_DESCONTO.search(texto):
            intel.criterio_julgamento = CriterioJulgamento.MAIOR_DESCONTO
            intel.criterio_julgamento_legivel = "Maior Desconto"
        elif self._padroes.CRIT_MENOR_PRECO_GRUPO.search(texto):
            intel.criterio_julgamento = CriterioJulgamento.MENOR_PRECO_GRUPO
            intel.criterio_julgamento_legivel = "Menor Preço por Grupo / Lote"
        elif self._padroes.CRIT_MENOR_PRECO_GLOBAL.search(texto):
            intel.criterio_julgamento = CriterioJulgamento.MENOR_PRECO_GLOBAL
            intel.criterio_julgamento_legivel = "Menor Preço Global"
        else:
            intel.criterio_julgamento = CriterioJulgamento.MENOR_PRECO_ITEM
            intel.criterio_julgamento_legivel = "Menor Preço por Item"
        
        resultado.regras.criterio_julgamento = intel.criterio_julgamento.value

        # ── 2. Modo de Disputa Operacional (IN 73/2022) ──────────────────────
        modo_str = resultado.regras.modo_disputa.lower()
        if modo_str in ("aberto_fechado", "aberto e fechado"):
            intel.modo_disputa = ModoDisputaDetalhado(
                tipo=TipoModoDisputa.ABERTO_FECHADO,
                nome_legivel="Aberto e Fechado (Art. 32 da IN 73/2022)",
                tempo_etapa_aberta_minutos=15,
                tempo_prorrogacao_minutos=2,
                tempo_etapa_fechada_minutos=10,
                percentual_etapa_fechada=10.0,
                max_propostas_etapa_fechada=3,
                descricao_operacional=(
                    "Etapa aberta de 15 minutos com prorrogações de 2 minutos se houver lance no final. "
                    "Na sequência, inicia-se a Etapa Fechada de 10 minutos (lances sigilosos) para o melhor lance "
                    "e todos os fornecedores até 10% superiores (ou as 3 melhores ofertas)."
                ),
            )
        elif modo_str in ("dispensa_eletronica", "dispensa"):
            intel.modo_disputa = ModoDisputaDetalhado(
                tipo=TipoModoDisputa.DISPENSA_ELETRONICA,
                nome_legivel="Dispensa Eletrônica (IN 67/2021)",
                tempo_etapa_aberta_minutos=360,
                tempo_prorrogacao_minutos=2,
                tempo_etapa_fechada_minutos=0,
                percentual_etapa_fechada=0.0,
                max_propostas_etapa_fechada=0,
                descricao_operacional=(
                    "Procedimento de Dispensa Eletrônica com período de envio de lances público "
                    "e encerramento com prorrogações automáticas de 2 minutos caso haja lances no término."
                ),
            )
        elif modo_str in ("fechado_aberto", "fechado e aberto"):
            intel.modo_disputa = ModoDisputaDetalhado(
                tipo=TipoModoDisputa.FECHADO_ABERTO,
                nome_legivel="Fechado e Aberto (Art. 33 da IN 73/2022)",
                tempo_etapa_aberta_minutos=10,
                tempo_prorrogacao_minutos=2,
                tempo_etapa_fechada_minutos=0,
                percentual_etapa_fechada=0.0,
                max_propostas_etapa_fechada=0,
                descricao_operacional=(
                    "Propostas iniciais sigilosas. Classificam-se para a etapa aberta o melhor lance "
                    "e propostas até 10% superiores para disputa com prorrogações sucessivas de 2 minutos."
                ),
            )
        else:
            intel.modo_disputa = ModoDisputaDetalhado(
                tipo=TipoModoDisputa.ABERTO,
                nome_legivel="Aberto (Art. 31 da IN 73/2022)",
                tempo_etapa_aberta_minutos=10,
                tempo_prorrogacao_minutos=2,
                tempo_etapa_fechada_minutos=0,
                percentual_etapa_fechada=0.0,
                max_propostas_etapa_fechada=0,
                descricao_operacional=(
                    "Etapa aberta de 10 minutos. Prorroga-se automaticamente por mais 2 minutos "
                    "sempre que houver lance nos últimos 2 minutos. Encerra sem etapa fechada após 2 min sem lances."
                ),
            )

        # ── 3. Intervalo Mínimo de Degrau entre Lances ───────────────────────
        dec_val = resultado.regras.decremento_global_valor
        dec_pct = resultado.regras.decremento_global_percent
        if dec_val is None and dec_pct is None:
            m_pct = self._padroes.DECREMENTO_PERCENT.search(texto)
            if m_pct:
                dec_pct = self._str_para_float(m_pct.group(1))
                resultado.regras.decremento_global_percent = dec_pct
            else:
                m_val = self._padroes.DECREMENTO_VALOR.search(texto)
                if m_val:
                    dec_val = self._str_para_float(m_val.group(1))
                    resultado.regras.decremento_global_valor = dec_val

        if dec_val and dec_val > 0:
            intel.intervalo_lances = IntervaloLances(
                decremento_minimo_reais=dec_val,
                decremento_minimo_percentual=None,
                intervalo_tempo_minimo_segundos=3,
                exige_decremento_proprio=True,
                descricao=f"Degrau obrigatório de no mínimo R$ {dec_val:.2f} por lance.",
            )
        elif dec_pct and dec_pct > 0:
            intel.intervalo_lances = IntervaloLances(
                decremento_minimo_reais=None,
                decremento_minimo_percentual=dec_pct,
                intervalo_tempo_minimo_segundos=3,
                exige_decremento_proprio=True,
                descricao=f"Degrau obrigatório de no mínimo {dec_pct:.2f}% por lance.",
            )
        else:
            intel.intervalo_lances = IntervaloLances(
                decremento_minimo_reais=0.01,
                decremento_minimo_percentual=None,
                intervalo_tempo_minimo_segundos=3,
                exige_decremento_proprio=True,
                descricao="Degrau padrão de R$ 0,01 (conforme regra geral Compras.gov.br).",
            )

        # ── 4. Horários e Sessão Pública (Horário Oficial de Brasília) ───────
        m_dt_hr = self._padroes.DATA_HORA_ABERTURA.search(texto)
        if m_dt_hr:
            intel.cronograma.data_abertura = m_dt_hr.group(1).strip()
            hora_raw = m_dt_hr.group(2).strip().replace("h", ":")
            intel.cronograma.hora_abertura = hora_raw if ":" in hora_raw else f"{hora_raw}:00"
        else:
            m_hr = self._padroes.HORA_BRASILIA.search(texto)
            if m_hr:
                hora_raw = m_hr.group(1).strip().replace("h", ":")
                intel.cronograma.hora_abertura = hora_raw if ":" in hora_raw else f"{hora_raw}:00"

        m_prazo = self._padroes.PRAZO_PROPOSTA_AJUSTADA.search(texto)
        if m_prazo:
            val_p = m_prazo.group(1).lower()
            if "duas" in val_p:
                intel.cronograma.prazo_envio_proposta_ajustada_horas = 2
            elif "três" in val_p or "tres" in val_p:
                intel.cronograma.prazo_envio_proposta_ajustada_horas = 3
            elif "quatro" in val_p:
                intel.cronograma.prazo_envio_proposta_ajustada_horas = 4
            elif val_p.isdigit():
                intel.cronograma.prazo_envio_proposta_ajustada_horas = int(val_p)

        # ── 5. Matriz de Riscos de Desclassificação e Conformidade ───────────
        riscos: list[RiscoDesclassificacao] = []

        # Risco 1: Desclassificação por valor superior ao estimado/máximo
        if self._padroes.VEDACAO_VALOR_MAXIMO.search(texto) or True:
            riscos.append(RiscoDesclassificacao(
                id_risco="RISCO_VALOR_MAXIMO",
                titulo="Valor Acima do Preço Máximo / Estimado",
                severidade=SeveridadeRisco.ALTO,
                artigo_legal="Art. 59, III da Lei nº 14.133/2021",
                descricao="Propostas ou lances finais que ultrapassarem o valor máximo fixado no edital serão desclassificados sumariamente pelo pregoeiro.",
                como_evitar="O robô utiliza o valor de referência como teto inegociável e não permite envio de lance inicial superior ao orçamento.",
            ))

        # Risco 2: Presunção de Inexequibilidade
        riscos.append(RiscoDesclassificacao(
                id_risco="RISCO_INEXEQUIBILIDADE",
                titulo="Presunção de Inexequibilidade de Preços",
                severidade=SeveridadeRisco.MEDIO,
                artigo_legal="Art. 59, § 4º da Lei nº 14.133/2021",
                descricao="Lances com desconto expressivo (abaixo de 50% em obras/serviços de engenharia ou com valor manifestamente inviável) exigem comprovação de custos perante o pregoeiro.",
                como_evitar="Configurar o 'Preço Mínimo (Stop-Loss)' rigorosamente com base no custo real da empresa e manter a Planilha de Custos / Composição pronta para envio.",
        ))

        # Risco 3: Prazo Regulamentar para Envio de Proposta Readequada
        riscos.append(RiscoDesclassificacao(
            id_risco="RISCO_PRAZO_PROPOSTA_AJUSTADA",
            titulo=f"Prazo de Envio da Proposta Ajustada ({intel.cronograma.prazo_envio_proposta_ajustada_horas}h após convocação)",
            severidade=SeveridadeRisco.ALTO,
            artigo_legal="Art. 56 da IN SEGES/ME nº 73/2022",
            descricao=f"Após ser declarado vencedor da disputa, o fornecedor tem o prazo improrrogável de {intel.cronograma.prazo_envio_proposta_ajustada_horas} horas para anexar no Compras.gov a proposta com os valores finais ajustados.",
            como_evitar="Acompanhar o chat da sessão imediatamente após o término dos lances e gerar a proposta final ajustada pelo sistema.",
        ))

        # Risco 4: Exigência de Declarações e Enquadramento ME/EPP
        riscos.append(RiscoDesclassificacao(
            id_risco="RISCO_DECLARACOES_SICAF",
            titulo="Declarações Obrigatórias e Enquadramento ME/EPP",
            severidade=SeveridadeRisco.MEDIO,
            artigo_legal="Art. 63 da Lei 14.133/2021 e LC nº 123/2006",
            descricao="Declaração indevida de ME/EPP sem preencher os requisitos de faturamento ou ausência de declarações obrigatórias no sistema enseja desclassificação e abertura de processo administrativo.",
            como_evitar="Verificar se as certidões e o porte da empresa no SICAF estão atualizados antes de ingressar na disputa de lances.",
        ))

        # Risco 5: Intervalo Temporal Mínimo entre Lances
        riscos.append(RiscoDesclassificacao(
            id_risco="RISCO_INTERVALO_LANCES",
            titulo="Intervalo Mínimo de Lances (Anti-Flood)",
            severidade=SeveridadeRisco.BAIXO,
            artigo_legal="Art. 28 da IN SEGES/ME nº 73/2022",
            descricao="O sistema Compras.gov rejeita lances intermediários que não superem o próprio lance anterior do fornecedor ou que sejam enviados em intervalo inferior a 3 segundos.",
            como_evitar="O robô calcula automaticamente o degrau legal e aplica o intervalo mínimo de segurança entre requisições.",
        ))

        intel.riscos_identificados = riscos
        logger.info(
            f"Inteligência Compras.gov cruzada: Critério={intel.criterio_julgamento_legivel} | "
            f"Modo={intel.modo_disputa.nome_legivel} | Riscos={len(riscos)}"
        )

    def _isolar_termo_referencia(self, texto: str) -> str:
        """
        Isola a seção do Termo de Referência / Anexo I / Quadro de Itens.
        """
        padroes_tr = [
            r"termo\s+de\s+refer[êe]ncia",
            r"anexo\s+i\b",
            r"quadro\s+demonstrativo\s+de\s+itens",
            r"especifica[çc][ãa]o\s+dos\s+itens",
            r"rela[çc][ãa]o\s+de\s+itens",
            r"descri[çc][ãa]o\s+dos\s+itens",
            r"planilha\s+de\s+(?:itens|pre[çc]os|quantitat)",
            r"tabela\s+\d+\s*[-–:]?\s*bens\s+e\s+servi[çc]os",
        ]

        pos_min = -1
        for p in padroes_tr:
            m = re.search(p, texto, re.IGNORECASE)
            if m:
                if pos_min == -1 or m.start() < pos_min:
                    pos_min = m.start()

        if pos_min != -1:
            logger.info("Seção do Termo de Referência / Anexo I identificada com sucesso.")
            return texto[pos_min:]

        return texto

    # ── Validação de Descrições ────────────────────────────────────────────────

    @staticmethod
    def _eh_texto_administrativo(desc: str) -> bool:
        """Retorna True se a descrição é texto administrativo/jurídico."""
        desc_norm = desc.lower().strip()

        # 1. Blacklist de substrings
        for bl in _BLACKLIST:
            if bl in desc_norm:
                return True

        # 2. Referência a lei/decreto (ex: "423/1992", "14.133/2021")
        if re.match(r"^\d[\d\.]*\s*/\s*\d{2,4}\b", desc.strip()):
            return True

        # 3. Contém verbos administrativos
        if _VERBOS_ADMIN.search(desc_norm):
            return True

        # 4. Começa com artigos/preposições típicos de cláusulas
        if re.match(
            r"^(?:a|o|as|os|para|nos|nas|no|na|em|quando|após|apos|somente|"
            r"exclusivamente|conforme|diante|fica|ficam|este|esta|neste|nesta)\s",
            desc_norm,
        ):
            return True

        return False

    # ── Extração de Itens ──────────────────────────────────────────────────────

    def _extrair_itens(self, texto_completo: str, resultado: ResultadoEdital, caminho_pdf: Optional[Path] = None) -> None:
        """Pipeline multi-estratégia para extração de itens."""
        texto_tr = self._isolar_termo_referencia(texto_completo)

        # ── Estratégia 1: Tabela estruturada de itens ────────────────────────
        # Formato: "Grupo Item Descrição CATSER Unidade Qtde. Valor..."
        # ou: "Item Descrição Un Qtd Valor..."
        itens = self._extrair_tabela_itens(texto_tr, resultado)
        if itens:
            resultado.itens = itens
            logger.info(f"Estratégia 1 (tabela estruturada): {len(itens)} item(ns) extraídos!")
            return

        # ── Estratégia 2: Linhas "ITEM N – descrição" com R$ próximo ────────
        itens = self._extrair_itens_explicitos(texto_tr, resultado)
        if itens:
            resultado.itens = itens
            logger.info(f"Estratégia 2 (ITEM explícito + R$): {len(itens)} item(ns) extraídos!")
            return

        # ── Estratégia 3: Gemini AI Fallback (Texto ou Multimodal PDF se escaneado) ───
        logger.info("RegEx não retornou itens válidos. Acionando Fallback Gemini AI...")
        self._extrair_itens_gemini(texto_tr, resultado, caminho_pdf)

    def _extrair_tabela_itens(self, texto: str, resultado: ResultadoEdital) -> list[ItemEdital]:
        """
        Estratégia 1: Detecta e extrai itens de tabelas estruturadas.
        
        Procura por cabeçalhos de tabela e depois processa linha a linha,
        acumulando descrição multilinha até encontrar R$ (que indica fim do item).
        """
        # Detectar cabeçalho de tabela de itens
        header_patterns = [
            r"(?:grupo\s+)?item\s+descri[çc][ãa]o\s+(?:catser\s+)?(?:unidade|un\.?|und\.?)\s+(?:qtd[e.]?|quantidade)",
            r"item\s+(?:n[º°.]?\s+)?descri[çc][ãa]o\s+(?:un\.?|unidade|und\.?)\s+(?:qtd[e.]?|quantidade)",
            r"item\s+descri[çc][ãa]o\s+(?:c[oó]digo\s+)?(?:un\.?|und\.?)\s+qtd",
            # Formato ALETO/SRP: "ITEM UND QTD DESCRIÇÃO" (descrição NO FINAL)
            r"\bITEM\s+(?:UND|unidade|und\.?)\s+\b(?:QTD|quantidade)\b\s+\bDESCRI[ÇC][ÃA]O\b",
        ]

        header_pos = -1
        for hp in header_patterns:
            m = re.search(hp, texto, re.IGNORECASE)
            if m:
                header_pos = m.end()
                logger.info(f"Cabeçalho de tabela detectado: '{texto[m.start():m.start()+80]}...'")
                break

        if header_pos == -1:
            return []

        # Extrair a região após o cabeçalho até "TOTAL GERAL"
        regiao_tabela = texto[header_pos:]
        fim_tabela = re.search(r"TOTAL\s+GERAL", regiao_tabela, re.IGNORECASE)
        if fim_tabela:
            regiao_tabela = regiao_tabela[:fim_tabela.start()]

        # Processar linha a linha
        linhas = regiao_tabela.split("\n")
        itens: list[ItemEdital] = []
        bloco_atual: list[str] = []
        num_item_atual: int = 0

        # Regex para detectar início de item: "GRUPO_NUM ITEM_NUM" ou "ITEM_NUM ..."
        # Ex: "1 1", "2 Suporte técnico...", "3 Licenças Windows..."
        re_inicio_item = re.compile(
            r"^\s*(?:(\d{1,2})\s+)?(\d{1,4})\s*(.*?)$"
        )

        # Regex para formato ALETO/SRP: "ITEM UND QTD DESCRIÇÃO" → linhas "NUM UND QTD Descrição..."
        # Ex: "01 UND 200 Adesivo instantâneo universal 20g...", "02 UND 1500 ..."
        re_inicio_item_aleto = re.compile(
            r"^\s*(\d{1,4})\s+(UND|unidade|und\.?)\s+(\d[\d.,]*)\s*(.*)$",
            re.IGNORECASE,
        )

        # Detectar qual formato de cabeçalho foi encontrado
        usar_formato_aleto = bool(re.search(
            r"\bITEM\s+(?:UND|unidade|und\.?)\s+\b(?:QTD|quantidade)\b\s+\bDESCRIÇÃO\b",
            texto, re.IGNORECASE,
        ))

        # Armazenar unidade e qtd do item atual para uso ao salvar item anterior
        unidade_atual: str | None = None
        qtd_atual: float | None = None

        for linha in linhas:
            linha_strip = linha.strip()

            # Ignorar linhas vazias, separadores e linhas de TOTAL parcial
            if not linha_strip or linha_strip == "-":
                continue
            if re.match(r"^TOTAL\s+GRUPO", linha_strip, re.IGNORECASE):
                # Fim de um grupo — salvar item pendente
                if num_item_atual > 0 and bloco_atual:
                    item = self._montar_item_tabela(num_item_atual, bloco_atual, resultado)
                    if item:
                        itens.append(item)
                    bloco_atual = []
                    num_item_atual = 0
                continue

            # Ignorar URLs e cabeçalhos de página repetidos
            if "http" in linha_strip.lower() or "SEI/TJ" in linha_strip:
                continue
            if re.match(r"^\d{2}/\d{2}/\d{4},\s+\d{2}:\d{2}", linha_strip):
                continue

            # Tentar detectar início de novo item
            if usar_formato_aleto:
                # Formato ALETO/SRP: "NUM UND QTD Descrição..." (ex: "01 UND 200 Adesivo...")
                m = re_inicio_item_aleto.match(linha_strip)
                if m:
                    item_num = int(m.group(1))
                    unidade_raw = m.group(2).upper()
                    qtd_val = self._str_para_float(m.group(3))
                    descricao_inicio = m.group(4).strip()

                    if 1 <= item_num <= 500:
                        # Salvar item anterior se existir — usar os dados DO ITEM ANTERIOR
                        if num_item_atual > 0 and bloco_atual:
                            item = self._montar_item_tabela(
                                num_item_atual, bloco_atual, resultado,
                                unidade=unidade_atual, qtd=qtd_atual
                            )
                            if item:
                                itens.append(item)
                            bloco_atual = []

                        num_item_atual = item_num
                        unidade_atual = unidade_raw
                        qtd_atual = qtd_val
                        # Sempre iniciar bloco com o item num, mesmo sem descrição na mesma linha
                        bloco_atual.append(str(item_num))
                        if descricao_inicio:
                            bloco_atual.append(descricao_inicio)
                        continue
            else:
                # Formato clássico: "NUM desc..." ou "GRUPO NUM ITEM NUM desc..."
                m = re_inicio_item.match(linha_strip)
                if m:
                    grupo_num = m.group(1)  # pode ser None
                    item_num_str = m.group(2)
                    resto = m.group(3).strip()

                    try:
                        item_num = int(item_num_str)
                    except ValueError:
                        # Não é número — acumular como parte da descrição
                        bloco_atual.append(linha_strip)
                        continue

                    if 1 <= item_num <= 500:
                        # Se já temos um item acumulado, salvar
                        if num_item_atual > 0 and bloco_atual:
                            item = self._montar_item_tabela(num_item_atual, bloco_atual, resultado)
                            if item:
                                itens.append(item)
                            bloco_atual = []

                        num_item_atual = item_num
                        if resto:
                            bloco_atual.append(resto)
                        continue

            # Acumular linha como parte da descrição do item atual
            if num_item_atual > 0:
                bloco_atual.append(linha_strip)

        # Salvar último item pendente
        if num_item_atual > 0 and bloco_atual:
            item = self._montar_item_tabela(num_item_atual, bloco_atual, resultado)
            if item:
                itens.append(item)

        itens.sort(key=lambda x: x.numero_item)
        return itens

    def _montar_item_tabela(
        self,
        num_item: int,
        linhas_bloco: list[str],
        resultado: ResultadoEdital,
        unidade: str | None = None,
        qtd: float | None = None,
    ) -> Optional[ItemEdital]:
        """Monta um ItemEdital a partir de linhas acumuladas da tabela.

        Suporta dois formatos:
        - Formato clássico (com R$ na linha): valor é extraído do texto
        - Formato ALETO/SRP (sem R$ — ata não preenchida): item é criado com valor=None
        """
        texto_bloco = " ".join(linhas_bloco)
        texto_bloco = re.sub(r"\s+", " ", texto_bloco).strip()

        if not texto_bloco:
            return None

        # Extrair valor de referência (primeiro R$ encontrado = unitário)
        val_ref = None
        m_val = self._padroes.VALOR_REAIS.search(texto_bloco)
        if m_val:
            val_ref = self._str_para_float(m_val.group(1))

        # SRP sem preços registrados: item sem R$ ainda é válido (valor será preenchido pelo licitante)
        # O valor None indica que o usuário precisa fornecer o preço de referência na GUI

        # Usar unidade e qtd informados (formato ALETO/SRP) ou extrair da tabela
        unidade_final = unidade if unidade is not None else "UN"

        if qtd is not None and qtd > 0:
            qtd_final = qtd
        else:
            qtd_final = 1.0

        # Tentar extrair unidade e qtd da estrutura da tabela (formato CATSER + Un + Qtd)
        # Só roda se não tivermos unidade/qtd informados pelo formato ALETO/SRP
        if unidade_final == "UN" and qtd_final == 1.0:
            m_tabela = re.search(
                r"\b\d{4,6}\s+"
                r"(un[d.]?|und|turma|hora|m[eê]s|servi[çc]o|kg|par|l(?:t)?|cx|pç|peça|resma|metro)\s+"
                r"(\d[\d.,]*)\s+"
                r"R\$",
                texto_bloco, re.IGNORECASE,
            )
            if m_tabela:
                uf = m_tabela.group(1).upper()
                if uf in ("UND", "UND.", "UN.", "UNID"):
                    uf = "UN"
                unidade_final = uf
                qtd_val = self._str_para_float(m_tabela.group(2))
                if qtd_val > 0:
                    qtd_final = qtd_val

        # Limpar descrição: remover código CATSER, unidade, qtd, R$ do texto

        # Limpar descrição: remover código CATSER, unidade, qtd, R$ do texto
        desc = texto_bloco
        # Remover bloco "CATSER Un Qtd R$ valor R$ valor" do final
        desc = re.sub(
            r"\s*\d{4,6}\s+(?:Un[d.]?|UND?|Turma|Hora|Mês|MES|Kg|Par)\s+\d[\d.,]*\s+R\$.*$",
            "", desc, flags=re.IGNORECASE
        )
        desc = re.sub(r"R\$\s*[\d.,]+", "", desc)
        desc = re.sub(r"\b\d{4,6}\b", "", desc)  # Códigos CATSER restantes
        desc = re.sub(r"\s+", " ", desc).strip()
        desc = desc.rstrip(".,;: ")
        desc = desc[:300]

        if len(desc) < 5:
            return None

        return ItemEdital(
            numero_item=num_item,
            descricao=desc,
            unidade_medida=unidade_final,
            quantidade=qtd_final,
            valor_referencia=val_ref,
            decremento_minimo=resultado.regras.decremento_global_valor,
            decremento_percentual=resultado.regras.decremento_global_percent,
        )

    def _extrair_itens_explicitos(self, texto: str, resultado: ResultadoEdital) -> list[ItemEdital]:
        itens: dict[int, ItemEdital] = {}

        item_re = re.compile(
            r"(?:^|\n)\s*item\s+(\d{1,4})\s*[-–.:)\]]\s*(.{10,300})",
            re.IGNORECASE,
        )

        for match in item_re.finditer(texto):
            try:
                num = int(match.group(1))
                desc_raw = match.group(2).strip()

                if num <= 0 or num > 500 or num in itens:
                    continue
                if self._eh_texto_administrativo(desc_raw):
                    continue

                # Contexto para valor R$
                ctx_end = min(match.start() + 600, len(texto))
                contexto = texto[match.start():ctx_end]

                # REQUER R$ no contexto próximo para confirmar que é um item real
                m_val = self._padroes.VALOR_REAIS.search(contexto)
                if not m_val:
                    continue

                val_ref = self._str_para_float(m_val.group(1))

                qtd = 1.0
                unidade = "UN"
                m_qtd = self._padroes.QUANTIDADE_UNIDADE.search(contexto)
                if m_qtd:
                    qtd_val = self._str_para_float(m_qtd.group(1))
                    if qtd_val > 0:
                        qtd = qtd_val
                    unidade = m_qtd.group(2).upper()

                desc_limpa = re.sub(r"\s+", " ", desc_raw)[:300]

                itens[num] = ItemEdital(
                    numero_item=num,
                    descricao=desc_limpa,
                    unidade_medida=unidade,
                    quantidade=qtd,
                    valor_referencia=val_ref if val_ref > 0 else None,
                    decremento_minimo=resultado.regras.decremento_global_valor,
                    decremento_percentual=resultado.regras.decremento_global_percent,
                )
            except (ValueError, IndexError):
                continue

        return sorted(itens.values(), key=lambda x: x.numero_item)

    def _extrair_itens_gemini(
        self,
        texto_tr: str,
        resultado: ResultadoEdital,
        caminho_pdf: Optional[Path] = None,
    ) -> None:
        """Extrai itens e metadados usando Gemini AI com Structured Output JSON (suporta texto e PDF visual escaneado)."""
        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("Nenhuma chave GEMINI_API_KEY configurada para extração via IA.")
            resultado.erros_parsing.append(
                "Nenhum item identificado via RegEx e chave GEMINI_API_KEY não configurada no .env."
            )
            return

        try:
            import time
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)

            prompt = (
                "Você é um especialista em licitações públicas brasileiras (Compras.gov.br / PNCP).\n"
                "Extraia as informações do edital / aviso de contratação e a lista completa de ITENS da tabela de bens/serviços (Termo de Referência).\n\n"
                "REGRAS OBRIGATÓRIAS:\n"
                "- Extraia SOMENTE itens reais de produtos ou serviços da tabela/relação do Termo de Referência.\n"
                "- Para cada item, extraia numero_item, descricao, quantidade, unidade_medida e valor_referencia.\n"
                "- IGNORE COMPLETAMENTE cláusulas jurídicas, regras de habilitação, sanções e procedimentos administrativos.\n\n"
                "Formato de resposta JSON obrigatório:\n"
                "{\n"
                '  "numero_pregao": "03/2026",\n'
                '  "numero_uasg": "928758",\n'
                '  "orgao": "Nome do Órgão ou Entidade",\n'
                '  "objeto": "Descrição sucinta do objeto da licitação",\n'
                '  "criterio_julgamento": "menor_preco_item",\n'
                '  "modo_disputa": "aberto",\n'
                '  "itens": [\n'
                '    {\n'
                '      "numero_item": 1,\n'
                '      "descricao": "Descrição clara do produto ou serviço",\n'
                '      "quantidade": 10.0,\n'
                '      "unidade_medida": "UN",\n'
                '      "valor_referencia": 150.00\n'
                '    }\n'
                '  ]\n'
                "}\n"
            )

            contents: list[Any] = []
            uploaded_file = None
            temp_slice_path = None

            # 1. Tentar upload de arquivo via File API se existir caminho físico
            if caminho_pdf and Path(caminho_pdf).exists():
                c_path = Path(caminho_pdf)
                try:
                    import tempfile
                    from pypdf import PdfWriter

                    reader = PdfReader(str(c_path))
                    max_pages = min(15, len(reader.pages))
                    
                    if len(reader.pages) > 15:
                        writer = PdfWriter()
                        for p_idx in range(max_pages):
                            writer.add_page(reader.pages[p_idx])
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_f:
                            writer.write(tmp_f)
                            temp_slice_path = tmp_f.name
                        upload_target = temp_slice_path
                    else:
                        upload_target = str(c_path)

                    # Upload via File API (ideal para PDFs escaneados de qualquer tamanho)
                    uploaded_file = client.files.upload(file=upload_target)
                    if uploaded_file and hasattr(uploaded_file, "name"):
                        contents = [uploaded_file, prompt]
                        logger.info(f"PDF enviado com sucesso via File API ({max_pages} págs): {uploaded_file.name}")
                except Exception as exc_file_api:
                    logger.warning(f"File API upload falhou, tentando fallback inline: {exc_file_api}")

            # 2. Fallback inline se File API não foi usada
            if not contents:
                pdf_bytes: Optional[bytes] = None
                if caminho_pdf and Path(caminho_pdf).exists():
                    try:
                        c_path = Path(caminho_pdf)
                        reader = PdfReader(str(c_path))
                        if len(reader.pages) > 15:
                            import io
                            from pypdf import PdfWriter
                            writer = PdfWriter()
                            for p_idx in range(min(15, len(reader.pages))):
                                writer.add_page(reader.pages[p_idx])
                            buf = io.BytesIO()
                            writer.write(buf)
                            pdf_bytes = buf.getvalue()
                        else:
                            with open(c_path, "rb") as f:
                                pdf_bytes = f.read()
                    except Exception as exc_read:
                        logger.warning(f"Não foi possível ler bytes do PDF para IA multimodal: {exc_read}")

                if pdf_bytes:
                    contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
                    contents.append(prompt)
                else:
                    contents.append(f"{prompt}\n\nTEXTO DO TERMO DE REFERÊNCIA:\n{texto_tr[:25000]}")

            modelos = [
                "gemini-3.5-flash",
                "gemini-flash-latest",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-3-flash-preview",
                "gemini-flash-lite-latest",
                "gemini-3.6-flash",
            ]
            response = None
            for mod in modelos:
                try:
                    logger.info(f"Tentando extração de edital via Gemini ({mod})...")
                    response = client.models.generate_content(
                        model=mod,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    if response and response.text:
                        logger.info(f"Gemini AI ({mod}) processou o documento com sucesso.")
                        break
                except Exception as exc_mod:
                    logger.warning(f"Tentativa com modelo {mod} falhou: {exc_mod}")
                    time.sleep(1.0)
                    continue

            # Limpar arquivo temporário local e no File API após processar
            if temp_slice_path:
                try:
                    Path(temp_slice_path).unlink(missing_ok=True)
                except Exception:
                    pass

            if uploaded_file and hasattr(uploaded_file, "name"):
                try:
                    if uploaded_file.name is not None:
                        client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

            if response and response.text:
                data = json.loads(response.text)
                itens_json = (
                    data.get("itens", [])
                    if isinstance(data, dict)
                    else (data if isinstance(data, list) else [])
                )

                # Se obteve metadados e os campos locais estavam vazios, preenche
                if isinstance(data, dict):
                    if not resultado.numero_uasg and data.get("numero_uasg"):
                        resultado.numero_uasg = str(data["numero_uasg"]).strip()
                    if not resultado.numero_pregao and data.get("numero_pregao"):
                        resultado.numero_pregao = str(data["numero_pregao"]).strip()
                    if not resultado.objeto and data.get("objeto"):
                        resultado.objeto = str(data["objeto"]).strip()
                    if not resultado.orgao and data.get("orgao"):
                        resultado.orgao = str(data["orgao"]).strip()
                    if data.get("criterio_julgamento") or data.get("modo_disputa"):
                        if data.get("modo_disputa"):
                            resultado.regras.modo_disputa = str(data["modo_disputa"]).strip()
                        self._cruzar_regras_compras_gov(resultado.regras.texto_raw or str(data), resultado)

                for i in itens_json:
                    if isinstance(i, dict) and "numero_item" in i and "descricao" in i:
                        desc = str(i["descricao"])[:300]
                        if self._eh_texto_administrativo(desc):
                            continue

                        val_ref = None
                        if i.get("valor_referencia") is not None:
                            try:
                                val_ref = float(i["valor_referencia"])
                            except (ValueError, TypeError):
                                val_ref = None

                        item = ItemEdital(
                            numero_item=int(i["numero_item"]),
                            descricao=desc,
                            unidade_medida=str(i.get("unidade_medida", "UN")).upper(),
                            quantidade=float(i.get("quantidade", 1.0)),
                            valor_referencia=val_ref,
                            decremento_minimo=resultado.regras.decremento_global_valor,
                            decremento_percentual=resultado.regras.decremento_global_percent,
                        )
                        resultado.itens.append(item)

                resultado.itens.sort(key=lambda x: x.numero_item)
                resultado.extracao_ia_usada = True
                if resultado.itens:
                    resultado.erros_parsing.clear()
                logger.info(f"✅ Gemini AI extraiu {len(resultado.itens)} item(ns) com sucesso!")

        except Exception as exc:
            logger.error(f"Erro ao extrair itens via Gemini AI: {exc}")
            resultado.erros_parsing.append(f"Erro no parsing via IA: {exc}")

    @staticmethod
    def _str_para_float(valor_str: str) -> float:
        valor_str = valor_str.strip().replace(" ", "")
        if "," in valor_str and "." in valor_str:
            valor_str = valor_str.replace(".", "").replace(",", ".")
        elif "," in valor_str:
            valor_str = valor_str.replace(",", ".")
        try:
            return float(valor_str)
        except ValueError:
            return 0.0

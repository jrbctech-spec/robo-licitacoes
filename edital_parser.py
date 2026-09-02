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
from pathlib import Path
from typing import Any, Optional

from pypdf import PdfReader

from config import settings

logger = logging.getLogger(__name__)


# ─── Verbos conjugados típicos de texto administrativo ─────────────────────────
_VERBOS_ADMIN = re.compile(
    r"\b(?:"
    r"será|sera|serão|serao|"
    r"deverá|devera|deverão|deverao|"
    r"poderá|podera|poderão|poderao|"
    r"ficará|ficara|"
    r"aplica[mn]?\-?se|"
    r"considera[mn]?\-?se|"
    r"entende\-?se|"
    r"encerrad[oa]|"
    r"fundamentad[oa]|"
    r"convocad[oa]|"
    r"adjudicad[oa]|"
    r"vinculad[oa]|"
    r"estabelecid[oa]"
    r")\b",
    re.IGNORECASE,
)

# ─── Blacklist de substrings que indicam texto administrativo ──────────────────
_BLACKLIST = frozenset([
    "do objeto", "da participação", "da participacao", "das condições", "das condicoes",
    "da habilitação", "da habilitacao", "do julgamento", "dos recursos",
    "das sanções", "das sancoes", "das obrigações", "das obrigacoes",
    "do pagamento", "da dotação", "da dotacao", "das disposições", "das disposicoes",
    "da impugnação", "da impugnacao", "do credenciamento",
    "do lance", "da proposta", "do contrato", "da ata",
    "dos direitos", "das penalidades", "da fraude", "dos anexos",
    "da vigência", "da vigencia", "do reajuste", "da subcontratação", "da subcontratacao",
    "do cadastramento", "da fiscalização", "da fiscalizacao",
    "do edital", "do pregão", "do pregao",
    "da licitação", "da licitacao", "da sessão", "da sessao",
    "da contratação", "da contratacao", "da execução", "da execucao",
    "poderão participar", "poderao participar",
    "a fase de lances", "a fase de lance",
    "para ter acesso", "para participar",
    "aplicam-se", "não se aplica", "nao se aplica",
    "o pregoeiro", "a pregoeira",
    "sistema eletrônico", "sistema eletronico",
    "nos termos d", "conforme disposto",
    "lei federal", "lei nº", "lei n°", "lei n.",
    "decreto nº", "decreto n°",
    "instrução normativa", "instrucao normativa",
    "o presente edital", "neste edital",
    "o certame", "processo licitatório", "processo licitatorio",
    "registro de preços", "registro de precos",
    "ata de registro",
    "habilitação jurídica", "habilitacao juridica",
    "regularidade fiscal",
    "certidão negativa", "certidao negativa",
    "diário oficial", "diario oficial",
    "cadastrar proposta", "acesso ao sistema",
    "modo de disputa", "critério de julgamento", "criterio de julgamento",
    "uso da senha", "responsabilidade exclusiva",
    "requisitos do fornecedor",
    "previsão de penalidades", "previsao de penalidades",
    "acordo de nível", "acordo de nivel",
    "art.", "inciso ", "parágrafo", "paragrafo",
])


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
    piso_preco: Optional[float] = None


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
    """Resultado completo do parsing de um edital."""
    caminho_pdf: str
    numero_pregao: str = ""
    numero_uasg: str = ""
    orgao: str = ""
    objeto: str = ""
    regras: RegraEdital = field(default_factory=RegraEdital)
    itens: list[ItemEdital] = field(default_factory=list)
    erros_parsing: list[str] = field(default_factory=list)
    sucesso: bool = False
    extracao_ia_usada: bool = False


class _Padroes:
    """Padrões compilados para extração de texto de editais."""
    NUMERO_PREGAO = re.compile(r"preg[ãa]o\s+(?:eletr[oô]nico\s+)?n[º°]?\s*(\d+/\d{4})", re.IGNORECASE)
    NUMERO_UASG = re.compile(r"(?:uasg|unidade\s+gestora(?:\s+compradora)?)\s*[:\-–]?\s*(\d{5,6})", re.IGNORECASE)
    OBJETO = re.compile(r"objeto[:\s]+([^\n]{20,300})", re.IGNORECASE)

    MODO_ABERTO = re.compile(r"modo\s+aberto", re.IGNORECASE)
    MODO_FECHADO = re.compile(r"modo\s+fechado", re.IGNORECASE)
    MODO_ABERTO_FECHADO = re.compile(r"modo\s+aberto\s+e\s+fechado", re.IGNORECASE)

    DECREMENTO_VALOR = re.compile(r"decremento\s+m[íi]nimo\s*(?:de\s*)?R?\$?\s*([\d.,]+)", re.IGNORECASE)
    DECREMENTO_PERCENT = re.compile(r"decremento\s+m[íi]nimo\s*(?:de\s*)?([\d.,]+)\s*%", re.IGNORECASE)

    QUANTIDADE_UNIDADE = re.compile(
        r"([\d.,]+)\s*(un(?:id(?:ade)?)?|und|kg|g\b|mg|ml|litro|l(?:t)?|cx|caixa|pç|peça|pe[cç]a"
        r"|servi[çc]o|hora|h\b|m[eê]s|pct|pacote|gl|gal[ãa]o|par|rl|rolo|fr|frasco"
        r"|amp|ampola|comp|comprimido|tb|tubo|fl|folha|resma|ct|cartucho"
        r"|ton|tonelada|saco|sc|bd|bobina|metro|m\b|turma)\b",
        re.IGNORECASE,
    )

    VALOR_REAIS = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)


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

        # Extração de itens (multi-estratégia + Gemini AI fallback)
        self._extrair_itens(texto_completo, resultado)

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
        elif self._padroes.MODO_FECHADO.search(texto):
            regras.modo_disputa = "fechado"
        elif self._padroes.MODO_ABERTO.search(texto):
            regras.modo_disputa = "aberto"

        m = self._padroes.DECREMENTO_PERCENT.search(texto)
        if m:
            regras.decremento_global_percent = self._str_para_float(m.group(1))

        if regras.decremento_global_percent is None:
            m = self._padroes.DECREMENTO_VALOR.search(texto)
            if m:
                regras.decremento_global_valor = self._str_para_float(m.group(1))

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

    def _extrair_itens(self, texto_completo: str, resultado: ResultadoEdital) -> None:
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

        # ── Estratégia 3: Gemini AI Fallback ─────────────────────────────────
        logger.info("RegEx não retornou itens válidos. Acionando Fallback Gemini AI...")
        self._extrair_itens_gemini(texto_tr, resultado)

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

                # Verificar se é um item plausível (1-500) e sequencial ou próximo
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
        self, num_item: int, linhas_bloco: list[str], resultado: ResultadoEdital
    ) -> Optional[ItemEdital]:
        """Monta um ItemEdital a partir de linhas acumuladas da tabela."""
        texto_bloco = " ".join(linhas_bloco)
        texto_bloco = re.sub(r"\s+", " ", texto_bloco).strip()

        if not texto_bloco:
            return None

        # Extrair valor de referência (primeiro R$ encontrado = unitário)
        val_ref = None
        m_val = self._padroes.VALOR_REAIS.search(texto_bloco)
        if m_val:
            val_ref = self._str_para_float(m_val.group(1))

        # Item sem R$ não é item real de tabela de preços
        if val_ref is None or val_ref <= 0:
            return None

        # Extrair unidade e quantidade da estrutura da tabela
        # Formato típico: "CATSER_CODE UNIDADE QTD R$ VALOR R$ TOTAL"
        # Ex: "27332 Und 1 R$ 154.509,50 R$ 154.509,50"
        # Ex: "27464 Und 24 R$ 14.900,35 R$ 357.608,40"
        qtd = 1.0
        unidade = "UN"

        # Padrão específico para tabelas de licitação: CATSER + Unidade + Qtd
        m_tabela = re.search(
            r"\b\d{4,6}\s+"
            r"(un[d.]?|und|turma|hora|m[eê]s|servi[çc]o|kg|par|l(?:t)?|cx|pç|peça|resma|metro)\s+"
            r"(\d[\d.,]*)\s+"
            r"R\$",
            texto_bloco, re.IGNORECASE,
        )
        if m_tabela:
            unidade = m_tabela.group(1).upper()
            if unidade in ("UND", "UND.", "UN.", "UNID"):
                unidade = "UN"
            qtd_val = self._str_para_float(m_tabela.group(2))
            if qtd_val > 0:
                qtd = qtd_val
        else:
            # Fallback: procurar unidade + qtd sem CATSER
            m_qtd = self._padroes.QUANTIDADE_UNIDADE.search(texto_bloco)
            if m_qtd:
                qtd_val = self._str_para_float(m_qtd.group(1))
                if qtd_val > 0:
                    qtd = qtd_val
                unidade = m_qtd.group(2).upper()
                if unidade in ("UND", "UND.", "UN.", "UNID"):
                    unidade = "UN"

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
            unidade_medida=unidade,
            quantidade=qtd,
            valor_referencia=val_ref,
            decremento_minimo=resultado.regras.decremento_global_valor,
            decremento_percentual=resultado.regras.decremento_global_percent,
        )

    def _extrair_itens_explicitos(self, texto: str, resultado: ResultadoEdital) -> list[ItemEdital]:
        """
        Estratégia 2: Linhas com prefixo "ITEM N" ou "Item N" que tenham R$ próximo.
        Só aceita itens que tenham valor monetário no contexto (forte sinal de item real).
        """
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

    def _extrair_itens_gemini(self, texto_tr: str, resultado: ResultadoEdital) -> None:
        """Extrai itens usando Gemini AI com Structured Output JSON (fallback)."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("Nenhuma chave GEMINI_API_KEY configurada para extração via IA.")
            resultado.erros_parsing.append(
                "Nenhum item identificado via RegEx e chave GEMINI_API_KEY não configurada."
            )
            return

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = (
                "Você é um especialista em licitações públicas brasileiras (Compras.gov.br).\n"
                "Extraia APENAS a lista de ITENS/LOTES de PRODUTOS ou SERVIÇOS do Termo de Referência abaixo.\n\n"
                "REGRAS OBRIGATÓRIAS:\n"
                "- Extraia SOMENTE itens da TABELA DE BENS E SERVIÇOS ou lista numerada de produtos/serviços.\n"
                "- Cada item deve representar um PRODUTO ou SERVIÇO REAL (ex: 'Licenças Windows Server', "
                "'Suporte técnico por 12 meses', 'Treinamento da equipe').\n"
                "- IGNORE COMPLETAMENTE: cláusulas jurídicas, regras de participação, habilitação, "
                "sanções, prazos, legislação, procedimentos administrativos.\n\n"
                "Para cada item extraia:\n"
                "- numero_item (inteiro sequencial)\n"
                "- descricao (string do PRODUTO ou SERVIÇO real)\n"
                "- quantidade (float)\n"
                "- unidade_medida (string ex: UN, KG, MES, TURMA)\n"
                "- valor_referencia (float do preço unitário de referência, se houver)\n\n"
                "Responda como JSON: {\"itens\": [...]}\n\n"
                "TEXTO:\n"
                f"{texto_tr[:15000]}"
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            if response.text:
                data = json.loads(response.text)
                itens_json = data.get("itens", []) if isinstance(data, dict) else data

                for i in itens_json:
                    if isinstance(i, dict) and "numero_item" in i and "descricao" in i:
                        desc = str(i["descricao"])[:300]
                        if self._eh_texto_administrativo(desc):
                            continue

                        item = ItemEdital(
                            numero_item=int(i["numero_item"]),
                            descricao=desc,
                            unidade_medida=str(i.get("unidade_medida", "UN")).upper(),
                            quantidade=float(i.get("quantidade", 1.0)),
                            valor_referencia=float(i["valor_referencia"]) if i.get("valor_referencia") else None,
                        )
                        resultado.itens.append(item)

                resultado.itens.sort(key=lambda x: x.numero_item)
                resultado.extracao_ia_usada = True
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

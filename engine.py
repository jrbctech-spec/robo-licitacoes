"""
engine.py – Motor de decisão de lances (100% determinístico e auditável).

PRINCÍPIOS DE SEGURANÇA:
  1. Trava Rígida (Preço Mínimo / Stop-Loss): Nenhum lance abaixo do preco_minimo por item é gerado.
     Esta verificação é dupla e atômica: no cálculo e na validação pré-envio.
  2. Latência Zero: Todas as regras são pré-carregadas em memória RAM (sem I/O em decisão).
  3. Precisão Financeira: Cálculos monetários com Decimal e arredondamento ROUND_HALF_UP (2 casas decimais).
  4. Separação Estrita: Meu Último Lance vs. Menor Lance do Processo vs. Preço Mínimo.
  5. Proteção Anti-Duplicidade: Flag atômica de lance_pendente e máquina de estados por item.
  6. Fail-Safe: Na dúvida ou em estado não confirmado, o robô NUNCA lança.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Enums e modelos de dados
# ─────────────────────────────────────────────────────────────────────────────

class TipoIntervalo(str, Enum):
    """Tipo de intervalo mínimo entre lances."""
    MONETARIO = "monetario"       # Valor fixo em Reais (ex: R$ 0,01 ou R$ 0,10)
    PERCENTUAL = "percentual"     # Percentual sobre o menor lance (ex: 0,5% ou 1,0%)


class EstadoItemEnum(str, Enum):
    """Máquina de estados independente para cada item na disputa."""
    AGUARDANDO = "AGUARDANDO"         # Aguardando início ou lances de concorrentes
    LIDERANDO = "LIDERANDO"           # Meu lance é atualmente o menor/melhor
    SUPERADO = "SUPERADO"             # Outro participante deu lance menor que o meu
    CALCULANDO = "CALCULANDO"         # Calculando próximo lance permitido
    VALIDANDO = "VALIDANDO"           # Validando intervalo, piso e regras
    ENVIANDO = "ENVIANDO"             # Lance em processo de envio no navegador
    CONFIRMANDO = "CONFIRMANDO"       # Aguardando confirmação do portal
    PISO_ATINGIDO = "PISO_ATINGIDO"   # Preço mínimo atingido – stop-loss acionado
    ERRO = "ERRO"                     # Erro na leitura ou envio do item
    ENCERRADO = "ENCERRADO"           # Item ou disputa encerrada pelo pregoeiro


class MotivoBloqueio(str, Enum):
    """Razão formal pela qual um lance foi bloqueado."""
    STOP_LOSS = "stop_loss"                       # Lance calculado < preço mínimo do item
    PISO_ATINGIDO = "piso_atingido"               # Preço mínimo atingido
    JA_VENCENDO = "ja_vencendo"                   # Já é o menor lance – não cobrir a si próprio
    LANCE_PENDENTE = "lance_pendente"             # Outro lance já em trânsito (anti-duplicidade)
    ITEM_INATIVO = "item_inativo"                 # Item não está aberto para lances
    DISPUTA_ENCERRADA = "disputa_encerrada"       # Sessão / disputa encerrada
    LANCE_INVALIDO = "lance_invalido"             # Lance calculado inválido (<= 0)
    SEM_CONCORRENTE = "sem_concorrente"           # Não há lance concorrente para cobrir
    MARGEM_INSUFICIENTE = "margem_insuficiente"   # Decremento impossível sem violar stop-loss
    TETO_EXCEDIDO = "teto_excedido"               # Lance acima do valor de referência
    INEXEQUIBILIDADE = "inexequibilidade"         # Risco de inexequibilidade (<50% do ref.)


class MotivoLance(str, Enum):
    """Razão pela qual um lance foi calculado."""
    DECREMENTO_MINIMO = "decremento_minimo_aplicado"
    DECREMENTO_PERCENTUAL = "decremento_percentual_aplicado"
    LANCE_INICIAL = "lance_inicial"
    COBRIR_LIDER = "cobrir_lider"


@dataclass(frozen=True)
class RegraItem:
    """
    Regras imutáveis de um item carregadas em memória antes da sessão.

    IMPORTANTE: Cada item possui seu próprio preço mínimo configurado pelo usuário.
    frozen=True garante que nenhum código acidental possa alterar o stop-loss durante a disputa.
    """
    item_id: str
    numero_item: int
    descricao: str
    custo_unitario: float                         # Custo real declarado (R$)
    margem_minima_percent: float                  # % mínima de margem
    preco_minimo: float                           # Stop-loss configurado para ESTE item
    preco_inicial_proposta: float                 # Proposta inicial cadastrada no certame
    intervalo_minimo: float = 0.01                # Valor ou % do intervalo
    tipo_intervalo: TipoIntervalo = TipoIntervalo.MONETARIO
    decremento_minimo: Optional[float] = None     # Alias de compatibilidade
    decremento_percentual: Optional[float] = None # Alias de compatibilidade
    valor_referencia: Optional[float] = None      # Valor estimado/teto do órgão
    bloquear_inexequivel: bool = False            # Bloqueia lances < 50% do valor de referência
    licitacao_id: str = ""

    def __post_init__(self) -> None:
        """Validação rigorosa de integridade das regras do item."""
        if self.custo_unitario <= 0:
            raise ValueError(
                f"Item {self.numero_item}: custo_unitario deve ser > 0, recebido: {self.custo_unitario}"
            )
        if self.preco_minimo < self.custo_unitario:
            raise ValueError(
                f"Item {self.numero_item}: preco_minimo ({self.preco_minimo}) não pode ser "
                f"inferior ao custo_unitario ({self.custo_unitario}). TRAVA DE SEGURANÇA ATIVADA."
            )
        if self.preco_inicial_proposta <= self.preco_minimo:
            raise ValueError(
                f"Item {self.numero_item}: preco_inicial_proposta ({self.preco_inicial_proposta}) "
                f"deve ser > preco_minimo ({self.preco_minimo})."
            )
        if self.margem_minima_percent < 0:
            raise ValueError(f"Item {self.numero_item}: margem_minima_percent não pode ser negativa.")

    @property
    def preco_minimo_decimal(self) -> Decimal:
        return Decimal(str(self.preco_minimo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def intervalo_minimo_decimal(self) -> Decimal:
        val = self.intervalo_minimo
        if self.decremento_minimo and self.decremento_minimo > 0:
            val = self.decremento_minimo
        elif self.decremento_percentual and self.decremento_percentual > 0:
            val = self.decremento_percentual
        return Decimal(str(val))


@dataclass
class DecisaoLance:
    """Resultado determinístico da decisão do motor de lances para um item."""
    item_id: str
    numero_item: int
    deve_dar_lance: bool                          # TRUE = enviar lance; FALSE = aguardar/bloquear
    valor_lance: Optional[float]                  # Valor do lance em float para API/GUI
    motivo: str                                   # Justificativa completa e auditável
    estado_resultante: EstadoItemEnum = EstadoItemEnum.AGUARDANDO
    bloqueado_stop_loss: bool = False             # TRUE se o preço mínimo atuou
    valor_stop_loss: Optional[float] = None
    motivo_bloqueio: Optional[MotivoBloqueio] = None
    meu_ultimo_lance: Optional[float] = None
    menor_lance_atual: Optional[float] = None
    posicao_atual: Optional[int] = None
    lance_concorrente: Optional[float] = None     # Alias para compatibilidade
    alerta_inexequibilidade: bool = False         # TRUE se o lance atingiu faixa de risco


# ─────────────────────────────────────────────────────────────────────────────
# Estado isolado e em tempo real por item
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EstadoItem:
    """Estado em tempo real de um item específico na disputa (isolado por item)."""
    regra: RegraItem
    estado_atual: EstadoItemEnum = EstadoItemEnum.AGUARDANDO
    menor_lance_atual: Optional[Decimal] = None   # Menor lance de qualquer participante no portal
    meu_ultimo_lance: Optional[Decimal] = None    # Meu último lance confirmado
    nossa_posicao: Optional[int] = None           # 1 = estamos liderando
    lance_pendente: bool = False                  # Flag atômica anti-duplicidade
    item_ativo: bool = True                       # True se o item está aberto para lances
    disputa_aberta: bool = True                   # True se a disputa geral do pregão está aberta
    total_lances_dados: int = 0                   # Lances confirmados nesta sessão
    encerrado: bool = False                       # Item encerrado pelo pregoeiro
    ultimo_motivo: str = ""
    ultima_atualizacao: float = field(default_factory=time.monotonic)

    # Propriedades para compatibilidade com código existente
    @property
    def melhor_lance_mercado(self) -> Optional[float]:
        return float(self.menor_lance_atual) if self.menor_lance_atual is not None else None

    @melhor_lance_mercado.setter
    def melhor_lance_mercado(self, valor: Optional[float]) -> None:
        if valor is not None:
            self.menor_lance_atual = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            self.menor_lance_atual = None


# ─────────────────────────────────────────────────────────────────────────────
# Motor de Decisão
# ─────────────────────────────────────────────────────────────────────────────

class MotorDecisao:
    """
    Motor determinístico de decisão de lances para pregões eletrônicos e dispensas.

    ARQUITETURA DE SEGURANÇA & FAIL-SAFE:
    - Estado de cada item totalmente isolado (nunca mistura item 1 com item 2).
    - Separação estrita: meu_ultimo_lance != menor_lance_atual.
    - Operações financeiras com precisão Decimal (ROUND_HALF_UP).
    - Proteção atômica contra lances duplicados (lance_pendente).
    - Trava dupla e inegociável de Preço Mínimo por Item.
    - Modo Simulação (dry-run) ativo por padrão.
    """

    def __init__(
        self,
        dry_run: bool = True,
        bloquear_inexequivel_padrao: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.bloquear_inexequivel_padrao = bloquear_inexequivel_padrao
        self._regras: dict[int, RegraItem] = {}       # numero_item → RegraItem
        self._estados: dict[int, EstadoItem] = {}     # numero_item → EstadoItem
        self._sessao_ativa = False
        self._historico_decisoes: list[DecisaoLance] = []

        if not dry_run:
            logger.warning(
                "⚠️  ATENÇÃO: Motor de decisão em MODO REAL. Lances serão enviados ao Compras.gov.br!"
            )
        else:
            logger.info(
                "🛡️  Motor de decisão em MODO SIMULAÇÃO (dry-run). Nenhum lance real será enviado."
            )

    # ── Configuração ──────────────────────────────────────────────────────────

    def carregar_regras(self, regras: list[RegraItem]) -> None:
        """
        Carrega as regras de todos os itens em memória RAM.
        Valida a integridade de cada item antes de permitir início da sessão.
        """
        erros: list[str] = []
        regras_validas: dict[int, RegraItem] = {}

        for regra in regras:
            try:
                regras_validas[regra.numero_item] = regra
                logger.debug(
                    f"  Item {regra.numero_item}: custo={regra.custo_unitario:.2f}, "
                    f"piso={regra.preco_minimo:.2f}, proposta_inicial={regra.preco_inicial_proposta:.2f}, "
                    f"intervalo={regra.intervalo_minimo} ({regra.tipo_intervalo.value})"
                )
            except Exception as exc:
                erros.append(f"Item {regra.numero_item}: {exc}")

        if erros:
            msg = "\n".join(erros)
            raise ValueError(f"Falha ao carregar regras dos itens:\n{msg}")

        self._regras = regras_validas
        self._estados = {
            num: EstadoItem(
                regra=r,
                meu_ultimo_lance=Decimal(str(r.preco_inicial_proposta)).quantize(Decimal("0.01")),
            )
            for num, r in regras_validas.items()
        }
        logger.info(f"✅ {len(self._regras)} regra(s) carregada(s) em memória RAM com precisão financeira.")

    def iniciar_sessao(self) -> None:
        """Marca o início de uma sessão de lances."""
        if not self._regras:
            raise RuntimeError("Nenhuma regra carregada. Chame carregar_regras() primeiro.")
        self._sessao_ativa = True
        self._historico_decisoes = []
        logger.info(
            f"🚀 Sessão iniciada | itens={len(self._regras)} | "
            f"modo={'SIMULAÇÃO' if self.dry_run else '🔴 REAL'}"
        )

    def encerrar_sessao(self) -> None:
        """Encerra a sessão atual e marca todos os itens como encerrados."""
        self._sessao_ativa = False
        for estado in self._estados.values():
            estado.encerrado = True
            estado.disputa_aberta = False
            estado.estado_atual = EstadoItemEnum.ENCERRADO
        logger.info(
            f"🏁 Sessão encerrada | decisões={len(self._historico_decisoes)} | "
            f"lances_dados={sum(e.total_lances_dados for e in self._estados.values())}"
        )

    # ── Atualização de estado (chamado pelo bot após cada polling) ───────────

    def atualizar_estado(
        self,
        numero_item: int,
        melhor_lance_mercado: Optional[float] = None,
        nossa_posicao: Optional[int] = None,
        nosso_ultimo_lance: Optional[float] = None,
        item_ativo: Optional[bool] = None,
        disputa_aberta: Optional[bool] = None,
    ) -> None:
        """
        Atualiza o estado em tempo real de um item específico.
        NÃO faz I/O de rede – apenas atualiza a estrutura em memória.
        """
        if numero_item not in self._estados:
            logger.warning(f"Item {numero_item} não encontrado nas regras carregadas.")
            return

        estado = self._estados[numero_item]
        estado.ultima_atualizacao = time.monotonic()

        if melhor_lance_mercado is not None and melhor_lance_mercado > 0:
            estado.menor_lance_atual = Decimal(str(melhor_lance_mercado)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if nossa_posicao is not None:
            estado.nossa_posicao = int(nossa_posicao)

        if nosso_ultimo_lance is not None and nosso_ultimo_lance > 0:
            estado.meu_ultimo_lance = Decimal(str(nosso_ultimo_lance)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if item_ativo is not None:
            estado.item_ativo = item_ativo
            if not item_ativo:
                estado.encerrado = True
                estado.estado_atual = EstadoItemEnum.ENCERRADO

        if disputa_aberta is not None:
            estado.disputa_aberta = disputa_aberta
            if not disputa_aberta:
                estado.encerrado = True
                estado.estado_atual = EstadoItemEnum.ENCERRADO

    # ── Métodos de Controle Anti-Duplicidade e Confirmação ────────────────────

    def marcar_lance_em_envio(self, numero_item: int) -> bool:
        """
        Ativa a trava atômica anti-duplicidade para o item.
        Retorna False se já houver lance pendente.
        """
        if numero_item not in self._estados:
            return False
        estado = self._estados[numero_item]
        if estado.lance_pendente:
            logger.warning(f"Item {numero_item} já possui lance pendente de confirmação. Envio bloqueado.")
            return False
        estado.lance_pendente = True
        estado.estado_atual = EstadoItemEnum.ENVIANDO
        return True

    def confirmar_lance_enviado(self, numero_item: int, valor_confirmado: float) -> None:
        """
        Confirma o envio do lance após retorno positivo do portal.
        Atualiza MEU ÚLTIMO LANCE e libera a trava de duplicidade.
        """
        if numero_item not in self._estados:
            return
        estado = self._estados[numero_item]
        estado.lance_pendente = False
        estado.meu_ultimo_lance = Decimal(str(valor_confirmado)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        estado.total_lances_dados += 1
        estado.estado_atual = EstadoItemEnum.CONFIRMANDO
        logger.info(
            f"✅ Lance CONFIRMADO pelo portal | Item {numero_item} | "
            f"Meu último lance atualizado para: R$ {estado.meu_ultimo_lance}"
        )

    def abortar_envio_lance(self, numero_item: int, motivo_erro: str = "") -> None:
        """Libera a trava de envio caso ocorra erro no portal."""
        if numero_item not in self._estados:
            return
        estado = self._estados[numero_item]
        estado.lance_pendente = False
        estado.estado_atual = EstadoItemEnum.ERRO
        estado.ultimo_motivo = motivo_erro
        logger.warning(f"⚠️ Envio de lance abortado | Item {numero_item} | Motivo: {motivo_erro}")

    # ── NÚCLEO DO MOTOR DE DECISÃO ───────────────────────────────────────────

    def calcular_lance(self, numero_item: int) -> DecisaoLance:
        """
        Toma a decisão determinística de lance para um item específico.

        REGRAS DE DECISÃO:
          1. SE disputa encerrada -> NÃO LANÇAR
          2. SE item inativo/encerrado -> NÃO LANÇAR
          3. SE houver lance pendente -> NÃO LANÇAR (anti-duplicidade)
          4. SE menor_lance_atual >= meu_ultimo_lance (ou posição 1) -> NÃO LANÇAR (LIDERANDO)
          5. SE menor_lance_atual < meu_ultimo_lance ->
             - Calcular próximo lance (monetário ou percentual) com precisão Decimal
             - SE novo_lance < preco_minimo -> NÃO LANÇAR (PISO_ATINGIDO)
             - SE novo_lance > valor_referencia -> NÃO LANÇAR (TETO_EXCEDIDO)
             - Enviar lance somente se todas as validações forem verdadeiras.
        """
        if not self._sessao_ativa:
            raise RuntimeError("Sessão não iniciada. Chame iniciar_sessao() primeiro.")

        if numero_item not in self._estados:
            raise ValueError(f"Item {numero_item} não encontrado nas regras carregadas.")

        estado = self._estados[numero_item]
        regra = estado.regra
        piso_decimal = regra.preco_minimo_decimal

        # ── 1. FAIL-SAFE: Disputa Encerrada ──────────────────────────────────
        if not estado.disputa_aberta or estado.encerrado:
            estado.estado_atual = EstadoItemEnum.ENCERRADO
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.DISPUTA_ENCERRADA,
                "Disputa ou item encerrado.",
                estado_res=EstadoItemEnum.ENCERRADO,
            )

        # ── 2. FAIL-SAFE: Item Inativo ───────────────────────────────────────
        if not estado.item_ativo:
            estado.estado_atual = EstadoItemEnum.ENCERRADO
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.ITEM_INATIVO,
                "Item inativo para lances no portal.",
                estado_res=EstadoItemEnum.ENCERRADO,
            )

        # ── 3. Proteção Anti-Duplicidade: Lance Pendente ─────────────────────
        if estado.lance_pendente:
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.LANCE_PENDENTE,
                "Lance anterior ainda em processamento/confirmação.",
                estado_res=EstadoItemEnum.ENVIANDO,
            )

        # ── 4. Estado de Mercado Não Identificado ────────────────────────────
        if estado.menor_lance_atual is None:
            estado.estado_atual = EstadoItemEnum.AGUARDANDO
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.SEM_CONCORRENTE,
                "Aguardando leitura do menor lance na disputa.",
                estado_res=EstadoItemEnum.AGUARDANDO,
            )

        menor_lance = estado.menor_lance_atual
        meu_lance = estado.meu_ultimo_lance

        # ── 5. SE já estamos liderando ou nosso lance é o menor ──────────────
        if estado.nossa_posicao == 1 or (meu_lance is not None and menor_lance >= meu_lance):
            estado.estado_atual = EstadoItemEnum.LIDERANDO
            val_txt = f"R$ {meu_lance:.2f}" if meu_lance else f"R$ {menor_lance:.2f}"
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.JA_VENCENDO,
                f"Liderando a disputa com {val_txt} (Posição 1).",
                lance_concorrente=float(menor_lance),
                meu_lance=float(meu_lance) if meu_lance else None,
                posicao=1,
                estado_res=EstadoItemEnum.LIDERANDO,
            )

        # ── 6. Item SUPERADO: Calcular próximo lance permitido ───────────────
        estado.estado_atual = EstadoItemEnum.CALCULANDO
        novo_lance_decimal, motivo_calc = self._calcular_proximo_lance_decimal(menor_lance, regra)

        # ── 7. VALIDAÇÃO DO PREÇO MÍNIMO (Stop-Loss do Item) ─────────────────
        estado.estado_atual = EstadoItemEnum.VALIDANDO
        if novo_lance_decimal < piso_decimal:
            estado.estado_atual = EstadoItemEnum.PISO_ATINGIDO
            motivo_piso = (
                f"PREÇO MÍNIMO ATINGIDO: Próximo lance R$ {novo_lance_decimal:.2f} seria inferior "
                f"ao preço mínimo configurado de R$ {piso_decimal:.2f}. "
                f"Margem mínima: {regra.margem_minima_percent}%."
            )
            logger.warning(f"🛑 {motivo_piso} | Item {numero_item}")
            decisao = DecisaoLance(
                item_id=regra.item_id,
                numero_item=numero_item,
                deve_dar_lance=False,
                valor_lance=None,
                motivo=motivo_piso,
                estado_resultante=EstadoItemEnum.PISO_ATINGIDO,
                bloqueado_stop_loss=True,
                valor_stop_loss=float(piso_decimal),
                motivo_bloqueio=MotivoBloqueio.STOP_LOSS,
                meu_ultimo_lance=float(meu_lance) if meu_lance else None,
                menor_lance_atual=float(menor_lance),
                lance_concorrente=float(menor_lance),
                posicao_atual=estado.nossa_posicao,
            )
            self._registrar_decisao(decisao)
            return decisao

        # ── 8. VALIDAÇÃO DO VALOR TETO / MÁXIMO (Art. 59, III Lei 14.133) ───
        if regra.valor_referencia and regra.valor_referencia > 0:
            ref_decimal = Decimal(str(regra.valor_referencia)).quantize(Decimal("0.01"))
            if novo_lance_decimal > ref_decimal:
                motivo_teto = (
                    f"VALOR ACIMA DO TETO: Lance R$ {novo_lance_decimal:.2f} ultrapassa o valor "
                    f"de referência de R$ {ref_decimal:.2f}. Bloqueado (Art. 59, III Lei 14.133/2021)."
                )
                logger.warning(f"🛑 {motivo_teto} | Item {numero_item}")
                return self._decisao_bloqueada(
                    regra,
                    MotivoBloqueio.TETO_EXCEDIDO,
                    motivo_teto,
                    lance_concorrente=float(menor_lance),
                    meu_lance=float(meu_lance) if meu_lance else None,
                    posicao=estado.nossa_posicao,
                    estado_res=EstadoItemEnum.ERRO,
                )

        # ── 9. VALIDAÇÃO DE INEXEQUIBILIDADE (<50% do valor de referência) ───
        alerta_inex = False
        if regra.valor_referencia and regra.valor_referencia > 0:
            limite_inex = (Decimal(str(regra.valor_referencia)) * Decimal("0.50")).quantize(Decimal("0.01"))
            if novo_lance_decimal < limite_inex:
                alerta_inex = True
                if regra.bloquear_inexequivel or self.bloquear_inexequivel_padrao:
                    motivo_inex = (
                        f"INEXEQUIBILIDADE: Lance R$ {novo_lance_decimal:.2f} é inferior a 50% do valor de "
                        f"referência (R$ {regra.valor_referencia:.2f}). Bloqueado para evitar "
                        f"desclassificação sumária (Art. 59 §4º Lei 14.133/2021)."
                    )
                    logger.warning(f"🛡️ {motivo_inex} | Item {numero_item}")
                    decisao = DecisaoLance(
                        item_id=regra.item_id,
                        numero_item=numero_item,
                        deve_dar_lance=False,
                        valor_lance=None,
                        motivo=motivo_inex,
                        estado_resultante=EstadoItemEnum.VALIDANDO,
                        bloqueado_stop_loss=False,
                        motivo_bloqueio=MotivoBloqueio.INEXEQUIBILIDADE,
                        meu_ultimo_lance=float(meu_lance) if meu_lance else None,
                        menor_lance_atual=float(menor_lance),
                        lance_concorrente=float(menor_lance),
                        posicao_atual=estado.nossa_posicao,
                        alerta_inexequibilidade=True,
                    )
                    self._registrar_decisao(decisao)
                    return decisao
                else:
                    motivo_calc += " | ⚠️ Risco Inexequibilidade (<50% do valor de ref. - Art. 59 §4º Lei 14.133)"

        # ── 10. DECISÃO FINAL: LANCE PERMITIDO ────────────────────────────────
        valor_final_float = float(novo_lance_decimal)
        decisao = DecisaoLance(
            item_id=regra.item_id,
            numero_item=numero_item,
            deve_dar_lance=True,
            valor_lance=valor_final_float,
            motivo=motivo_calc,
            estado_resultante=EstadoItemEnum.VALIDANDO,
            bloqueado_stop_loss=False,
            meu_ultimo_lance=float(meu_lance) if meu_lance else None,
            menor_lance_atual=float(menor_lance),
            lance_concorrente=float(menor_lance),
            posicao_atual=estado.nossa_posicao,
            alerta_inexequibilidade=alerta_inex,
        )

        logger.info(
            f"🎯 LANCE CALCULADO | Item {numero_item} | "
            f"Novo Lance: R$ {novo_lance_decimal:.2f} | Menor Concorrente: R$ {menor_lance:.2f} | "
            f"Piso: R$ {piso_decimal:.2f} | {'[SIMULAÇÃO]' if self.dry_run else '[REAL]'} | {motivo_calc}"
        )

        self._registrar_decisao(decisao)
        return decisao

    # ── Validação defensiva pré-envio (segunda linha de defesa) ───────────────

    def validar_lance_pre_envio(self, numero_item: int, valor: float) -> bool:
        """
        Verificação atômica e redundante antes do clique no portal.
        Garante que NUNCA um valor abaixo do preço mínimo seja enviado.
        """
        if numero_item not in self._regras:
            logger.error(f"Validação pré-envio: Item {numero_item} não encontrado.")
            return False

        regra = self._regras[numero_item]
        valor_decimal = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        piso_decimal = regra.preco_minimo_decimal

        if valor_decimal < piso_decimal:
            logger.critical(
                f"🚨 BLOQUEIO CRÍTICO PRÉ-ENVIO | Item {numero_item} | "
                f"Valor R$ {valor_decimal:.2f} < Preço Mínimo R$ {piso_decimal:.2f}. "
                "LANCE NÃO ENVIADO."
            )
            return False

        if valor_decimal <= Decimal("0.00"):
            logger.critical(f"🚨 BLOQUEIO CRÍTICO PRÉ-ENVIO | Valor inválido: R$ {valor_decimal:.2f}")
            return False

        return True

    # ── Helpers de cálculo financeiro ─────────────────────────────────────────

    def _calcular_proximo_lance_decimal(
        self,
        menor_lance: Decimal,
        regra: RegraItem,
    ) -> tuple[Decimal, str]:
        """Calcula o próximo lance respeitando o tipo de intervalo e regras."""
        # Determina o tipo e valor do intervalo
        tipo = regra.tipo_intervalo
        intervalo = regra.intervalo_minimo_decimal

        if regra.decremento_percentual and regra.decremento_percentual > 0:
            tipo = TipoIntervalo.PERCENTUAL
            intervalo = Decimal(str(regra.decremento_percentual))
        elif regra.decremento_minimo and regra.decremento_minimo > 0:
            tipo = TipoIntervalo.MONETARIO
            intervalo = Decimal(str(regra.decremento_minimo))

        if tipo == TipoIntervalo.PERCENTUAL:
            dec_val = (menor_lance * (intervalo / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if dec_val < Decimal("0.01"):
                dec_val = Decimal("0.01")
            novo_lance = (menor_lance - dec_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            motivo = f"Intervalo percentual de {intervalo}% (R$ {dec_val:.2f}) aplicado sobre menor lance R$ {menor_lance:.2f}"
            return novo_lance, motivo
        else:
            # Intervalo monetário em Reais
            novo_lance = (menor_lance - intervalo).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            motivo = f"Intervalo monetário de R$ {intervalo:.2f} aplicado sobre menor lance R$ {menor_lance:.2f}"
            return novo_lance, motivo

    def _decisao_bloqueada(
        self,
        regra: RegraItem,
        motivo_bloqueio: MotivoBloqueio,
        motivo: str,
        lance_concorrente: Optional[float] = None,
        meu_lance: Optional[float] = None,
        posicao: Optional[int] = None,
        estado_res: EstadoItemEnum = EstadoItemEnum.AGUARDANDO,
    ) -> DecisaoLance:
        """Cria e registra uma DecisaoLance de bloqueio / não envio."""
        decisao = DecisaoLance(
            item_id=regra.item_id,
            numero_item=regra.numero_item,
            deve_dar_lance=False,
            valor_lance=None,
            motivo=motivo,
            estado_resultante=estado_res,
            motivo_bloqueio=motivo_bloqueio,
            meu_ultimo_lance=meu_lance,
            menor_lance_atual=lance_concorrente,
            lance_concorrente=lance_concorrente,
            posicao_atual=posicao,
        )
        self._registrar_decisao(decisao)
        return decisao

    def _registrar_decisao(self, decisao: DecisaoLance) -> None:
        """Adiciona a decisão ao histórico em memória."""
        self._historico_decisoes.append(decisao)

    @staticmethod
    def _arredondar(valor: float, casas: int = 2) -> float:
        d = Decimal(str(valor)).quantize(
            Decimal("0." + "0" * casas),
            rounding=ROUND_HALF_UP,
        )
        return float(d)

    def relatorio_sessao(self) -> dict:
        """Gera relatório resumido da sessão atual."""
        total = len(self._historico_decisoes)
        lances = [d for d in self._historico_decisoes if d.deve_dar_lance]
        bloqueios = [d for d in self._historico_decisoes if d.bloqueado_stop_loss]
        pisos = [d for d in self._historico_decisoes if d.estado_resultante == EstadoItemEnum.PISO_ATINGIDO]

        return {
            "modo": "simulação" if self.dry_run else "real",
            "total_decisoes": total,
            "lances_calculados": len(lances),
            "bloqueios_stop_loss": len(bloqueios),
            "pisos_atingidos": len(pisos),
            "itens_ativos": len(self._regras),
            "itens_encerrados": sum(1 for e in self._estados.values() if e.encerrado),
        }

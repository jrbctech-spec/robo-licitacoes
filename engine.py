"""
engine.py – Motor de decisão de lances (100% determinístico).

PRINCÍPIOS DE SEGURANÇA:
  1. Trava Rígida (Stop-Loss): Nenhum lance abaixo do preço_minimo é gerado.
     Esta verificação é dupla: no cálculo E antes do envio.
  2. Latência Zero: Todas as regras são pré-carregadas em memória RAM.
     Nenhuma consulta de rede síncrona ocorre durante a decisão de lance.
  3. Imutabilidade: As regras de custo carregadas são imutáveis (frozen dataclass).
  4. Auditabilidade: Toda decisão é registrada com motivo detalhado.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Enums e modelos de dados
# ─────────────────────────────────────────────────────────────────────────────

class MotivoBloqueio(str, Enum):
    """Razão pela qual um lance foi bloqueado."""
    STOP_LOSS = "stop_loss"                    # Lance abaixo do preço mínimo
    JA_VENCENDO = "ja_vencendo"               # Já é o menor lance – não precisa cobrir
    LANCE_INVALIDO = "lance_invalido"         # Lance calculado é inválido (≤ 0)
    SEM_CONCORRENTE = "sem_concorrente"       # Não há lance concorrente para cobrir
    MARGEM_INSUFICIENTE = "margem_insuficiente" # Decremento impossível sem violar stop-loss


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

    IMPORTANTE: frozen=True garante que nenhum código acidental
    possa alterar o stop-loss durante a sessão de lances.
    """
    item_id: str
    numero_item: int
    descricao: str
    custo_unitario: float             # Custo real declarado (R$)
    margem_minima_percent: float      # % mínima de lucro
    preco_minimo: float               # Stop-loss = custo * (1 + margem/100)
    preco_inicial_proposta: float     # Proposta inicial enviada (R$)
    decremento_minimo: Optional[float] = None    # R$ mínimo de decremento por lance
    decremento_percentual: Optional[float] = None # % de decremento por lance
    licitacao_id: str = ""

    def __post_init__(self) -> None:
        """Validação de integridade das regras carregadas."""
        if self.custo_unitario <= 0:
            raise ValueError(
                f"Item {self.numero_item}: custo_unitario deve ser > 0, "
                f"recebido: {self.custo_unitario}"
            )
        if self.preco_minimo < self.custo_unitario:
            raise ValueError(
                f"Item {self.numero_item}: preco_minimo ({self.preco_minimo}) "
                f"não pode ser inferior ao custo_unitario ({self.custo_unitario}). "
                "TRAVA DE SEGURANÇA ATIVADA."
            )
        if self.preco_inicial_proposta <= self.preco_minimo:
            raise ValueError(
                f"Item {self.numero_item}: preco_inicial_proposta "
                f"({self.preco_inicial_proposta}) deve ser > preco_minimo "
                f"({self.preco_minimo})."
            )
        if self.margem_minima_percent < 0:
            raise ValueError("margem_minima_percent não pode ser negativa.")


@dataclass
class DecisaoLance:
    """Resultado da decisão do motor de lances para um item."""
    item_id: str
    numero_item: int
    deve_dar_lance: bool              # TRUE = enviar lance; FALSE = aguardar
    valor_lance: Optional[float]      # Valor calculado (None se bloqueado)
    motivo: str                       # Descrição da decisão
    bloqueado_stop_loss: bool = False # TRUE se a trava de segurança atuou
    valor_stop_loss: Optional[float] = None
    motivo_bloqueio: Optional[MotivoBloqueio] = None
    posicao_atual: Optional[int] = None
    lance_concorrente: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Estado mutável em memória durante a sessão
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EstadoItem:
    """Estado em tempo real de um item durante a sessão de lances."""
    regra: RegraItem
    melhor_lance_mercado: Optional[float] = None  # Menor lance da concorrência
    nossa_posicao: Optional[int] = None           # Nossa posição atual no ranking
    nosso_ultimo_lance: Optional[float] = None    # Último lance que enviamos
    total_lances_dados: int = 0                   # Contador de lances na sessão
    encerrado: bool = False                       # Item encerrado/arrematado


# ─────────────────────────────────────────────────────────────────────────────
# Motor de Decisão
# ─────────────────────────────────────────────────────────────────────────────

class MotorDecisao:
    """
    Motor determinístico de decisão de lances para pregões eletrônicos.

    ARQUITETURA DE SEGURANÇA:
    - Todas as regras são carregadas em RAM antes da sessão (sem I/O em decisão).
    - A verificação de stop-loss é executada em DOIS pontos independentes:
      (1) no cálculo do valor de lance e (2) na validação pré-envio.
    - O modo simulação (dry-run) está ativo por padrão.

    Uso:
        motor = MotorDecisao(dry_run=True)
        motor.carregar_regras([regra_item_1, regra_item_2])
        motor.iniciar_sessao()

        # Durante a sessão (em loop de polling da página):
        motor.atualizar_estado(numero_item=1, melhor_lance_mercado=95.50, posicao=2)
        decisao = motor.calcular_lance(numero_item=1)
        if decisao.deve_dar_lance:
            print(f"Lance: R$ {decisao.valor_lance:.2f}")
    """

    def __init__(self, dry_run: bool = True) -> None:
        """
        Inicializa o motor de decisão.

        Args:
            dry_run: Se True (padrão), nenhum sinal de lance real é emitido.
                     Sempre inicie em True; mude para False com confirmação explícita.
        """
        self.dry_run = dry_run
        self._regras: dict[int, RegraItem] = {}       # numero_item → RegraItem
        self._estados: dict[int, EstadoItem] = {}     # numero_item → EstadoItem
        self._sessao_ativa = False
        self._historico_decisoes: list[DecisaoLance] = []

        if not dry_run:
            logger.warning(
                "⚠️  ATENÇÃO: Motor de decisão em MODO REAL. "
                "Lances serão enviados ao Compras.gov.br!"
            )
        else:
            logger.info("🛡️  Motor de decisão em MODO SIMULAÇÃO (dry-run). "
                        "Nenhum lance real será enviado.")

    # ── Configuração ──────────────────────────────────────────────────────────

    def carregar_regras(self, regras: list[RegraItem]) -> None:
        """
        Carrega as regras de todos os itens em memória RAM.

        DEVE ser chamado ANTES de iniciar_sessao().
        Valida a integridade de cada regra antes de carregar.

        Args:
            regras: Lista de RegraItem com custos e stop-losses.

        Raises:
            ValueError: Se alguma regra for inválida.
        """
        erros: list[str] = []
        regras_validas: dict[int, RegraItem] = {}

        for regra in regras:
            try:
                # A validação já ocorre no __post_init__ do frozen dataclass
                regras_validas[regra.numero_item] = regra
                logger.debug(
                    f"  Item {regra.numero_item}: "
                    f"custo={regra.custo_unitario:.4f}, "
                    f"stop_loss={regra.preco_minimo:.4f}, "
                    f"proposta_inicial={regra.preco_inicial_proposta:.4f}"
                )
            except ValueError as exc:
                erros.append(str(exc))

        if erros:
            msg = "\n".join(erros)
            raise ValueError(
                f"Falha ao carregar regras – corrija os erros abaixo:\n{msg}"
            )

        self._regras = regras_validas
        self._estados = {
            num: EstadoItem(regra=r) for num, r in regras_validas.items()
        }
        logger.info(f"✅ {len(self._regras)} regra(s) carregada(s) em memória RAM.")

    def iniciar_sessao(self) -> None:
        """Marca o início de uma sessão de lances. Requer regras carregadas."""
        if not self._regras:
            raise RuntimeError(
                "Nenhuma regra carregada. Chame carregar_regras() antes de iniciar a sessão."
            )
        self._sessao_ativa = True
        self._historico_decisoes = []
        logger.info(
            f"🚀 Sessão iniciada | itens={len(self._regras)} | "
            f"modo={'SIMULAÇÃO' if self.dry_run else '🔴 REAL'}"
        )

    def encerrar_sessao(self) -> None:
        """Encerra a sessão atual."""
        self._sessao_ativa = False
        logger.info(
            f"🏁 Sessão encerrada | "
            f"decisões={len(self._historico_decisoes)} | "
            f"lances_dados={sum(e.total_lances_dados for e in self._estados.values())}"
        )

    # ── Atualização de estado (chamado pelo bot a cada polling) ───────────────

    def atualizar_estado(
        self,
        numero_item: int,
        melhor_lance_mercado: Optional[float] = None,
        nossa_posicao: Optional[int] = None,
        nosso_ultimo_lance: Optional[float] = None,
    ) -> None:
        """
        Atualiza o estado em tempo real de um item.

        Este método é chamado pelo browser_bot após cada leitura da página.
        NÃO faz I/O de rede – apenas atualiza a estrutura em memória.

        Args:
            numero_item: Número do item no pregão.
            melhor_lance_mercado: Menor lance atual da concorrência (R$).
            nossa_posicao: Nossa posição no ranking (1 = liderando).
            nosso_ultimo_lance: Valor do último lance que enviamos.
        """
        if numero_item not in self._estados:
            logger.warning(f"Item {numero_item} não encontrado nas regras carregadas.")
            return

        estado = self._estados[numero_item]
        if melhor_lance_mercado is not None:
            estado.melhor_lance_mercado = melhor_lance_mercado
        if nossa_posicao is not None:
            estado.nossa_posicao = nossa_posicao
        if nosso_ultimo_lance is not None:
            estado.nosso_ultimo_lance = nosso_ultimo_lance

    # ── Decisão de Lance (núcleo do motor) ───────────────────────────────────

    def calcular_lance(self, numero_item: int) -> DecisaoLance:
        """
        Calcula se deve dar um lance e qual o valor, para um item específico.

        ALGORITMO:
          1. Verificar se item existe e sessão está ativa.
          2. Se já estamos liderando (posição 1), aguardar.
          3. Calcular lance candidato = melhor_lance_concorrente - decremento.
          4. TRAVA DUPLA: rejeitar se lance_candidato < preco_minimo.
          5. Retornar DecisaoLance com motivo detalhado.

        Args:
            numero_item: Número do item para calcular.

        Returns:
            DecisaoLance com o resultado da decisão.
        """
        if not self._sessao_ativa:
            raise RuntimeError("Sessão não iniciada. Chame iniciar_sessao() primeiro.")

        if numero_item not in self._estados:
            raise ValueError(f"Item {numero_item} não encontrado nas regras carregadas.")

        estado = self._estados[numero_item]
        regra = estado.regra

        # ── Caso 1: Item encerrado ─────────────────────────────────────────────
        if estado.encerrado:
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.LANCE_INVALIDO,
                "Item encerrado.",
                lance_concorrente=estado.melhor_lance_mercado,
            )

        # ── Caso 2: Sem concorrente – lance inicial ───────────────────────────
        if estado.melhor_lance_mercado is None:
            # Sem informação de mercado → proposta inicial já foi enviada
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.SEM_CONCORRENTE,
                "Aguardando lance dos concorrentes.",
            )

        # ── Caso 3: Já estamos liderando ─────────────────────────────────────
        if estado.nossa_posicao == 1:
            val_str = (
                f"R$ {estado.nosso_ultimo_lance:.4f}"
                if estado.nosso_ultimo_lance is not None
                else "(proposta inicial)"
            )
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.JA_VENCENDO,
                f"Posição 1 – liderando com {val_str}.",
                lance_concorrente=estado.melhor_lance_mercado,
                posicao=1,
            )

        # ── Cálculo do lance candidato ─────────────────────────────────────────
        lance_base = estado.melhor_lance_mercado
        lance_candidato, motivo_calculo = self._calcular_valor_candidato(
            lance_base=lance_base,
            regra=regra,
        )

        # ── TRAVA DUPLA DE STOP-LOSS ──────────────────────────────────────────
        # Verificação 1: no valor calculado
        if lance_candidato < regra.preco_minimo:
            logger.warning(
                f"🛑 STOP-LOSS ATIVADO | Item {numero_item} | "
                f"Lance calculado: R$ {lance_candidato:.4f} < "
                f"Preço mínimo: R$ {regra.preco_minimo:.4f}"
            )
            decisao = DecisaoLance(
                item_id=regra.item_id,
                numero_item=numero_item,
                deve_dar_lance=False,
                valor_lance=None,
                motivo=(
                    f"STOP-LOSS: Lance calculado R$ {lance_candidato:.4f} seria "
                    f"inferior ao preço mínimo R$ {regra.preco_minimo:.4f}. "
                    f"Lance BLOQUEADO. Margem mínima: {regra.margem_minima_percent}%."
                ),
                bloqueado_stop_loss=True,
                valor_stop_loss=regra.preco_minimo,
                motivo_bloqueio=MotivoBloqueio.STOP_LOSS,
                lance_concorrente=lance_base,
                posicao_atual=estado.nossa_posicao,
            )
            self._registrar_decisao(decisao)
            return decisao

        # ── Verificação 2: validar o valor final (defesa extra) ───────────────
        lance_final = self._arredondar(lance_candidato)
        if lance_final <= 0:
            return self._decisao_bloqueada(
                regra,
                MotivoBloqueio.LANCE_INVALIDO,
                f"Lance calculado inválido: R$ {lance_final}",
            )

        # ── DECISÃO: DAR O LANCE ──────────────────────────────────────────────
        estado.total_lances_dados += 1
        decisao = DecisaoLance(
            item_id=regra.item_id,
            numero_item=numero_item,
            deve_dar_lance=True,
            valor_lance=lance_final,
            motivo=motivo_calculo,
            bloqueado_stop_loss=False,
            lance_concorrente=lance_base,
            posicao_atual=estado.nossa_posicao,
        )

        logger.info(
            f"✅ LANCE CALCULADO | Item {numero_item} | "
            f"R$ {lance_final:.4f} | Base: R$ {lance_base:.4f} | "
            f"{'[SIMULAÇÃO]' if self.dry_run else '[REAL]'} | {motivo_calculo}"
        )

        self._registrar_decisao(decisao)
        return decisao

    def validar_lance_pre_envio(self, numero_item: int, valor: float) -> bool:
        """
        Verificação defensiva final antes de qualquer envio ao navegador.

        Esta é a SEGUNDA camada de stop-loss – executada imediatamente
        antes do clique no botão de envio, como última linha de defesa.

        Args:
            numero_item: Número do item.
            valor: Valor do lance que será enviado.

        Returns:
            True se o lance é seguro para envio. False se deve ser bloqueado.
        """
        if numero_item not in self._regras:
            logger.error(f"Validação pré-envio: Item {numero_item} não encontrado.")
            return False

        regra = self._regras[numero_item]

        if valor < regra.preco_minimo:
            logger.critical(
                f"🚨 BLOQUEIO CRÍTICO PRÉ-ENVIO | Item {numero_item} | "
                f"Valor {valor:.4f} < Stop-Loss {regra.preco_minimo:.4f}. "
                "Lance NÃO enviado."
            )
            return False

        if valor <= 0:
            logger.critical(
                f"🚨 BLOQUEIO CRÍTICO PRÉ-ENVIO | Valor inválido: {valor}"
            )
            return False

        return True

    # ── Helpers privados ──────────────────────────────────────────────────────

    def _calcular_valor_candidato(
        self,
        lance_base: float,
        regra: RegraItem,
    ) -> tuple[float, str]:
        """
        Calcula o valor candidato do lance baseado nas regras do edital.

        Prioridade:
          1. Decremento percentual (se configurado)
          2. Decremento mínimo em reais
          3. Decremento padrão de 0,01 centavos

        Returns:
            Tupla (valor_candidato, motivo_descricao)
        """
        if regra.decremento_percentual and regra.decremento_percentual > 0:
            decremento = lance_base * (regra.decremento_percentual / 100)
            candidato = lance_base - decremento
            motivo = (
                f"Decremento percentual de {regra.decremento_percentual}% "
                f"= R$ {decremento:.4f} aplicado sobre R$ {lance_base:.4f}"
            )
            return candidato, motivo

        if regra.decremento_minimo and regra.decremento_minimo > 0:
            candidato = lance_base - regra.decremento_minimo
            motivo = (
                f"Decremento mínimo de R$ {regra.decremento_minimo:.4f} "
                f"aplicado sobre R$ {lance_base:.4f}"
            )
            return candidato, motivo

        # Fallback: decremento mínimo simbólico (1 centavo)
        candidato = lance_base - 0.01
        motivo = (
            f"Decremento padrão de R$ 0,01 aplicado "
            f"(nenhum decremento mínimo configurado)"
        )
        return candidato, motivo

    def _decisao_bloqueada(
        self,
        regra: RegraItem,
        motivo_bloqueio: MotivoBloqueio,
        motivo: str,
        lance_concorrente: Optional[float] = None,
        posicao: Optional[int] = None,
    ) -> DecisaoLance:
        """Cria e registra uma DecisaoLance de bloqueio."""
        decisao = DecisaoLance(
            item_id=regra.item_id,
            numero_item=regra.numero_item,
            deve_dar_lance=False,
            valor_lance=None,
            motivo=motivo,
            motivo_bloqueio=motivo_bloqueio,
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
        """Arredonda um valor para o número de casas decimais especificado."""
        d = Decimal(str(valor)).quantize(
            Decimal("0." + "0" * casas),
            rounding=ROUND_HALF_UP,
        )
        return float(d)

    # ── Relatório de sessão ───────────────────────────────────────────────────

    def relatorio_sessao(self) -> dict:
        """
        Gera um relatório resumido da sessão atual.

        Returns:
            Dicionário com estatísticas da sessão.
        """
        total = len(self._historico_decisoes)
        lances = [d for d in self._historico_decisoes if d.deve_dar_lance]
        bloqueios = [d for d in self._historico_decisoes if d.bloqueado_stop_loss]

        return {
            "modo": "simulação" if self.dry_run else "real",
            "total_decisoes": total,
            "lances_calculados": len(lances),
            "bloqueios_stop_loss": len(bloqueios),
            "itens_ativos": len(self._regras),
            "itens_encerrados": sum(
                1 for e in self._estados.values() if e.encerrado
            ),
        }

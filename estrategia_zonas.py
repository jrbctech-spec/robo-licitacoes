"""
estrategia_zonas.py – Régua de zonas para o Robô de Licitações.

A camada de estratégia que envolve o MotorDecisao e decide a postura
de lance (agressivo/padrão/conservador/bloqueado) com base na zona.

Zonas (definidas pelo percentual do stop-loss que o lance candidato
representa):
  - Eliminatória  : lance >= 80% do stop-loss → agressivo
  - Competitiva   : lance entre 60% e 80% do stop-loss → padrão
  - Preservação   : lance < 60% do stop-loss → conservador
  - Desistida     : lance abaixo do stop-loss → bloqueado
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from engine import DecisaoLance, EstadoItemEnum, MotorDecisao, RegraItem

logger = logging.getLogger(__name__)


class Zona(Enum):
    ELIMINATORIA = "eliminatoria"   # lance >= 80% do stop-loss
    COMPETITIVA  = "competitiva"    # lance entre 60% e 80% do stop-loss
    PRESERVACAO  = "preservacao"    # lance < 60% do stop-loss
    DESISTIDA    = "desistida"      # lance abaixo do stop-loss


class Estrategia(Enum):
    AGRESSIVO    = "agressivo"
    PADRAO       = "padrao"
    CONSERVADOR  = "conservador"
    BLOQUEADO    = "bloqueado"


# ─────────────────────────────────────────────────────────────────────────────
# DETERMINAR ZONA DO LANCE
# ─────────────────────────────────────────────────────────────────────────────

def determinar_zona(lance: float, stop_loss: float) -> Zona:
    """
    Determina em qual zona o lance se enquadra, baseado no percentual
    do stop-loss que o lance representa.
    """
    if stop_loss <= 0:
        return Zona.DESISTIDA
    if lance < stop_loss:
        return Zona.DESISTIDA
    pct = (lance / stop_loss) * 100.0
    if pct >= 80.0:
        return Zona.ELIMINATORIA
    if pct >= 60.0:
        return Zona.COMPETITIVA
    return Zona.PRESERVACAO


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR COM ESTRATÉGIA DE ZONAS
# ─────────────────────────────────────────────────────────────────────────────

class MotorComZonas(MotorDecisao):
    """
    MotorDecisao envolvido por estratégia de zonas.

    Por padrão usa a estratégia agressiva para zona eliminatória,
    padrão para zona competitiva, conservador para zona de preservação,
    e bloqueia qualquer lance abaixo do stop-loss (zona desistida).

    As estratégias podem ser configuradas com set_estrategia(zona, estrategia).
    """

    def __init__(
        self,
        *args,
        estrategia_zona_eliminatoria: Estrategia = Estrategia.AGRESSIVO,
        estrategia_zona_competitiva: Estrategia = Estrategia.PADRAO,
        estrategia_zona_preservacao: Estrategia = Estrategia.CONSERVADOR,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._estrategia_zona_eliminatoria = estrategia_zona_eliminatoria
        self._estrategia_zona_competitiva  = estrategia_zona_competitiva
        self._estrategia_zona_preservacao  = estrategia_zona_preservacao

        logger.info(
            "MotorComZonas iniciado | zona_elim=%s | zona_comp=%s | zona_pres=%s",
            estrategia_zona_eliminatoria.value,
            estrategia_zona_competitiva.value,
            estrategia_zona_preservacao.value,
        )

    def set_estrategia(self, zona: Zona, estrategia: Estrategia) -> None:
        if zona == Zona.ELIMINATORIA:
            self._estrategia_zona_eliminatoria = estrategia
        elif zona == Zona.COMPETITIVA:
            self._estrategia_zona_competitiva = estrategia
        elif zona == Zona.PRESERVACAO:
            self._estrategia_zona_preservacao = estrategia
        logger.debug("Estratégia da zona %s adjustada para %s", zona.value, estrategia.value)

    def calcular_lance(self, numero_item: int) -> DecisaoLance:
        """
        Calcula lance com estratégia de zonas: primeiro obtém o lance do
        MotorDecisao base, depois aplica a estratégia da zona.
        """
        if not self._sessao_ativa:
            raise RuntimeError("Sessão não iniciada")

        if numero_item not in self._estados:
            raise ValueError(f"Item {numero_item} não carregado")

        estado = self._estados[numero_item]
        regra = estado.regra

        # Se já está piso atingido ou encerrado, bloqueia direto
        if estado.estado_atual == EstadoItemEnum.PISO_ATINGIDO or estado.encerrado:
            return super().calcular_lance(numero_item)

        # Obtém o lance bruto do motor original
        decisao_bruta = super().calcular_lance(numero_item)

        # Se o motor bloqueou (não deve dar lance), retorna bloqueado
        if not decisao_bruta.deve_dar_lance:
            return decisao_bruta

        lance = decisao_bruta.valor_lance
        if lance is None:
            return decisao_bruta

        # Determina a zona do lance candidato
        zona = determinar_zona(lance, regra.preco_minimo)

        # Obtém a estratégia para aquela zona
        estrategia = _obter_estrategia(
            zona,
            self._estrategia_zona_eliminatoria,
            self._estrategia_zona_competitiva,
            self._estrategia_zona_preservacao,
        )

        # Aplica a estratégia
        return _aplicar_estrategia(estrategia, decisao_bruta, regra, zona, numero_item)


def _obter_estrategia(
    zona: Zona,
    estr_elim: Estrategia,
    estr_comp: Estrategia,
    estr_pres: Estrategia,
) -> Estrategia:
    if zona == Zona.ELIMINATORIA:
        return estr_elim
    if zona == Zona.COMPETITIVA:
        return estr_comp
    if zona == Zona.PRESERVACAO:
        return estr_pres
    return Estrategia.BLOQUEADO


def _aplicar_estrategia(
    estrategia: Estrategia,
    decisao: DecisaoLance,
    regra: RegraItem,
    zona: Zona,
    numero_item: int,
) -> DecisaoLance:
    """
    Aplica a estratégia desejada ao lance candidato.

    - AGRESSIVO  : mantém o lance (ou tenta decremento maior)
    - PADRAO     : mantém o lance calculado
    - CONSERVADOR: pode ajustar para um lance mais seguro (maior)
    - BLOQUEADO  : não envia, mesmo que o motor tivesse calculado
    """
    if estrategia == Estrategia.BLOQUEADO:
        logger.info(
            "Zona %s: estratégia BLOQUEADO – lance R$ %.2f não será enviado | Item %d",
            zona.value, decisao.valor_lance, numero_item,
        )
        decisao.deve_dar_lance = False
        decisao.motivo = (
            f"Zona {zona.value}: estratégia BLOQUEADO. "
            f"Lance R$ {decisao.valor_lance:.2f} não será enviado."
        )
        decisao.estado_resultante = EstadoItemEnum.PISO_ATINGIDO
        decisao.bloqueado_stop_loss = True
        decisao.motivo_bloqueio = None  # manter compatibilidade
        return decisao

    if estrategia == Estrategia.CONSERVADOR:
        # Ajusta para um lance mais conservador (maior)
        if decisao.valor_lance and decisao.valor_lance > regra.preco_minimo:
            # Diminui a agressividade: faz o lance ser 5% mais alto que o calculado
            # Isso é uma forma de "cobertura": se o lance for muito baixo, sobe um pouco
            lance_original = decisao.valor_lance
            lance_novo = round(lance_original * 1.05, 2)
            if lance_novo > regra.preco_minimo:
                logger.info(
                    "Zona %s: estratégia CONSERVADOR – ajustando lance de R$ %.2f para R$ %.2f | Item %d",
                    zona.value, lance_original, lance_novo, numero_item,
                )
                decisao.valor_lance = lance_novo
                decisao.motivo += f" | Ajuste conservador: R$ {lance_novo:.2f} (+5%)"

    # Estratégias AGRESSIVO e PADRAO: mantêm o lance calculado pelo motor
    logger.debug(
        "Zona %s: estratégia %s – lance R$ %.2f Enviado | Item %d",
        zona.value, estrategia.value, decisao.valor_lance, numero_item,
    )
    return decisao


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO DE ZONAS PARA ANÁLISE
# ─────────────────────────────────────────────────────────────────────────────

def analisar_zonas(motor: MotorComZonas, numero_item: int) -> Optional[dict]:
    """
    Analisa em qual zona um item estaria, dado o lance atual e o stop-loss.
    Retorna dict com: zona, pct_stop_loss, estrategia, lance_atual, stop_loss.
    """
    if numero_item not in motor._estados:
        return None

    estado = motor._estados[numero_item]
    regra = estado.regra
    lance_atual = float(estado.meu_ultimo_lance) if estado.meu_ultimo_lance else regra.preco_inicial_proposta
    stop_loss = regra.preco_minimo

    zona = determinar_zona(lance_atual, stop_loss)
    pct = (lance_atual / stop_loss * 100.0) if stop_loss > 0 else 0.0
    estrategia = _obter_estrategia(
        zona,
        motor._estrategia_zona_eliminatoria,
        motor._estrategia_zona_competitiva,
        motor._estrategia_zona_preservacao,
    )

    return {
        "item": numero_item,
        "lance_atual": lance_atual,
        "stop_loss": stop_loss,
        "pct_stop_loss": round(pct, 2),
        "zona": zona,
        "estrategia": estrategia,
        "zona_eliminatoria_pct": 80.0,
        "zona_competitiva_min_pct": 60.0,
        "zona_competitiva_max_pct": 80.0,
        "zona_preservacao_max_pct": 60.0,
    }

"""
notifier_manager.py – Gerenciador de eventos e alertas de liderança da JRBC TECH.

Detecta em tempo real transições de status (ex: LIDERANDO -> SUPERADO)
e aciona alertas sonoros locais imediatos e notificações push via Telegram.
"""
from __future__ import annotations

import logging
import threading
from decimal import Decimal
from typing import Any, Optional

from som_alerta import tocar_alerta_perda_lideranca
from telegram_service import TelegramService

logger = logging.getLogger(__name__)


class NotifierManager:
    def __init__(self, telegram_client: Any = None) -> None:
        self.telegram = telegram_client or TelegramService
        self.estados_itens: dict[str, bool] = {}  # {item_id: liderando_anteriormente}

    def verificar_mudanca_lideranca(
        self,
        item_id: str | int,
        liderando_agora: bool,
        melhor_mercado: Optional[float | Decimal] = None,
    ) -> None:
        """
        Verifica se houve ultrapassagem em um item.
        Se a empresa estava liderando e foi superada (anterior: True -> atual: False):
          1. Aciona alerta sonoro local no Linux (via paplay com som do freedesktop ou '\a' no stdout).
          2. Chama TelegramService.alerta_ultrapassagem(num_item, melhor_valor).
        """
        chave = str(item_id)
        estado_anterior = self.estados_itens.get(chave)

        # Transição crítica: Estávamos liderando (True) e agora não estamos mais (False)
        if estado_anterior is True and not liderando_agora:
            val_num = float(melhor_mercado) if melhor_mercado is not None else 0.0
            val_formatado = f"{val_num:,.2f}" if melhor_mercado is not None else "N/A"
            logger.warning(
                f"🚨 [ALERTA] JRBC TECH FOI SUPERADA NO ITEM {chave}! Melhor Mercado: R$ {val_formatado}"
            )

            # 1. Alarme Sonoro Local Imediato no Linux (paplay com som do freedesktop ou \a)
            tocar_alerta_perda_lideranca()

            # 2. Chamada a TelegramService.alerta_ultrapassagem em background
            def _push_telegram():
                try:
                    TelegramService.alerta_ultrapassagem(
                        num_item=chave,
                        melhor_valor=val_num,
                        orgao="Comprasnet Serpro"
                    )
                except Exception as e:
                    logger.debug(f"Aviso no envio de push Telegram: {e}")

            threading.Thread(target=_push_telegram, daemon=True).start()

        # Atualiza o estado atual do item
        self.estados_itens[chave] = liderando_agora


# Instância global singleton
notifier_manager = NotifierManager()

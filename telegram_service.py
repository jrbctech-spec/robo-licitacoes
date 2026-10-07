"""
telegram_service.py – Gerenciador de Envio de Notificações via Telegram.
JRBC TECH - CNPJ: 65.488.003/0001-61 - Palmas/TO

Responsável por:
- Enviar mensagens e cards formatados em Markdown para o canal/operador.
- Notificar alertas prioritários de perda de liderança (ultrapassagem).
- Confirmar lances disparados e status de disputa.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("TelegramService")


class TelegramService:
    """
    Serviço central de comunicação com a API do Telegram Bot.
    Lê as credenciais de forma segura exclusivamente do ambiente (.env).
    """
    TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
    CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
    BASE_URL: str = f"https://api.telegram.org/bot{TOKEN}"
    SSL_CTX: ssl.SSLContext = ssl._create_unverified_context()

    @classmethod
    def recarregar_credenciais(cls) -> None:
        """Recarrega as variáveis de ambiente caso tenham sido alteradas."""
        load_dotenv(override=True)
        cls.TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
        cls.CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
        cls.BASE_URL = f"https://api.telegram.org/bot{cls.TOKEN}"

    @classmethod
    def enviar_mensagem(cls, texto: str) -> bool:
        """Envia mensagem formatada em Markdown para o operador."""
        if not cls.TOKEN or not cls.CHAT_ID:
            cls.recarregar_credenciais()

        if not cls.TOKEN or not cls.CHAT_ID:
            logger.error("[TELEGRAM ERRO] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID ausentes no .env")
            return False

        url = f"{cls.BASE_URL}/sendMessage"
        payload = {
            "chat_id": cls.CHAT_ID,
            "text": texto,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            dados = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=cls.SSL_CTX, timeout=8) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                ok = bool(resultado.get("ok", False))
                if ok:
                    logger.debug("[TELEGRAM] Mensagem entregue com sucesso.")
                return ok
        except Exception as e:
            logger.error(f"[TELEGRAM ERRO] Falha ao enviar mensagem: {e}")
            return False

    @classmethod
    def alerta_ultrapassagem(cls, num_item: str, melhor_valor: float, orgao: str = "Comprasnet") -> bool:
        """Disparo prioritário quando a JRBC TECH perde o 1º lugar."""
        msg = (
            f"🚨 *ALERTA DE DISPUTA: 1º LUGAR PERDIDO!*\n\n"
            f"🏢 *Ambiente:* {orgao}\n"
            f"📦 *Item:* {num_item}\n"
            f"📉 *Melhor Lance Mercado:* R$ {melhor_valor:,.2f}\n\n"
            f"⚡ *Ação:* O robô está recalculando o contra-lance dentro do stop-loss."
        )
        return cls.enviar_mensagem(msg)

    @classmethod
    def alerta_lance_enviado(cls, num_item: str, valor_lance: float, status: str = "Liderando") -> bool:
        """Notifica envio com sucesso de novo lance."""
        msg = (
            f"🎯 *LANCE DISPARADO (JRBC TECH)*\n\n"
            f"📦 *Item:* {num_item}\n"
            f"💰 *Nosso Lance:* R$ {valor_lance:,.2f}\n"
            f"📊 *Status:* {status}"
        )
        return cls.enviar_mensagem(msg)

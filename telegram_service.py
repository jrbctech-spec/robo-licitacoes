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
import threading
import time
import urllib.request
from typing import Any, Optional

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
    def enviar_mensagem(cls, texto: str, reply_markup: Optional[dict] = None) -> bool:
        """Envia mensagem formatada em Markdown para o operador, com suporte opcional a botões Inline."""
        if not cls.TOKEN or not cls.CHAT_ID:
            cls.recarregar_credenciais()

        if not cls.TOKEN or not cls.CHAT_ID:
            logger.error("[TELEGRAM ERRO] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID ausentes no .env")
            return False

        url = f"{cls.BASE_URL}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": cls.CHAT_ID,
            "text": texto,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            dados = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=cls.SSL_CTX, timeout=10) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                ok = bool(resultado.get("ok", False))
                if ok:
                    logger.debug("[TELEGRAM] Mensagem entregue com sucesso.")
                return ok
        except Exception as e:
            logger.error(f"[TELEGRAM ERRO] Falha ao enviar mensagem: {e}")
            return False

    @classmethod
    def answer_callback_query(cls, callback_query_id: str, text: str, show_alert: bool = False) -> bool:
        """Responde a uma callback query do Telegram para confirmar interação do usuário."""
        if not cls.TOKEN:
            cls.recarregar_credenciais()
        if not cls.TOKEN:
            return False

        url = f"{cls.BASE_URL}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        }
        try:
            dados = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=cls.SSL_CTX, timeout=10) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                return bool(resultado.get("ok", False))
        except Exception as e:
            logger.warning(f"[TELEGRAM] Erro ao responder callback_query {callback_query_id}: {e}")
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


# ─────────────────────────────────────────────────────────────────────────────
# Listener de Callbacks Interativos do Telegram
# ─────────────────────────────────────────────────────────────────────────────

_listener_running = False
_listener_thread: Optional[threading.Thread] = None


def _loop_listener_telegram() -> None:
    """Loop daemon em background de long polling com getUpdates para processar botões inline."""
    global _listener_running
    logger.info("🛰️ [TELEGRAM LISTENER] Iniciando monitoramento de callbacks com long polling...")
    offset = 0

    while _listener_running:
        try:
            if not TelegramService.TOKEN:
                TelegramService.recarregar_credenciais()
            if not TelegramService.TOKEN:
                time.sleep(5)
                continue

            url = f"{TelegramService.BASE_URL}/getUpdates?offset={offset}&timeout=25&allowed_updates=%5B%22callback_query%22%5D"
            req = urllib.request.Request(url, headers={"User-Agent": "JRBC-Tech-Bot/1.0"})

            with urllib.request.urlopen(req, context=TelegramService.SSL_CTX, timeout=35) as resp:
                data_raw = resp.read().decode("utf-8")
                res = json.loads(data_raw)

                if not res.get("ok"):
                    time.sleep(2)
                    continue

                updates = res.get("result", [])
                for u in updates:
                    update_id = u.get("update_id", 0)
                    if update_id >= offset:
                        offset = update_id + 1

                    cq = u.get("callback_query")
                    if not cq:
                        continue

                    cq_id = cq.get("id")
                    callback_data = cq.get("data", "")
                    logger.info(f"[TELEGRAM LISTENER] Callback recebido: {callback_data}")

                    try:
                        from supabase_client import atualizar_status_licitacao

                        if callback_data.startswith("aprovar:"):
                            lic_id = callback_data.split(":", 1)[1]
                            atualizar_status_licitacao(lic_id, "aprovado_disputa")
                            TelegramService.answer_callback_query(
                                cq_id,
                                "✅ Edital aprovado para disputa! Já na sua aba de Aprovados.",
                                show_alert=True,
                            )
                            TelegramService.enviar_mensagem(
                                f"🎯 *CERTAME APROVADO REMOTAMENTE*\n\n"
                                f"Identificador: `{lic_id}`\n"
                                f"Status atualizado para: *aprovado_disputa*\n"
                                f"O certame já está agendado e visível na aba *🎯 Aprovados para Participação*!"
                            )

                        elif callback_data.startswith("triar:servico:"):
                            lic_id = callback_data.split(":", 2)[2]
                            atualizar_status_licitacao(lic_id, "triado_servico")
                            TelegramService.answer_callback_query(
                                cq_id,
                                "🛠️ Movido para a aba Serviços.",
                                show_alert=False,
                            )
                            TelegramService.enviar_mensagem(
                                f"🛠️ *TRIAGEM REMOTA DE SERVIÇO*\n\n"
                                f"Identificador: `{lic_id}`\n"
                                f"Status atualizado para: *triado_servico*\n"
                                f"Movido com sucesso para a aba *🛠️ Serviços*."
                            )

                        elif callback_data.startswith("triar:misto:"):
                            lic_id = callback_data.split(":", 2)[2]
                            atualizar_status_licitacao(lic_id, "triado_misto")
                            TelegramService.answer_callback_query(
                                cq_id,
                                "📦 Movido para Serviço com Produtos.",
                                show_alert=False,
                            )
                            TelegramService.enviar_mensagem(
                                f"📦 *TRIAGEM REMOTA: SERVIÇO COM PRODUTOS*\n\n"
                                f"Identificador: `{lic_id}`\n"
                                f"Status atualizado para: *triado_misto*\n"
                                f"Movido com sucesso para a aba *📦 Serviço com Produtos*."
                            )
                        else:
                            TelegramService.answer_callback_query(cq_id, "Comando desconhecido.")

                    except Exception as exc_act:
                        logger.error(f"[TELEGRAM LISTENER] Erro ao processar callback '{callback_data}': {exc_act}")
                        try:
                            TelegramService.answer_callback_query(cq_id, f"⚠️ Erro ao processar: {exc_act}", show_alert=True)
                        except Exception:
                            pass

        except Exception as err:
            logger.debug(f"[TELEGRAM LISTENER] Conexão/timeout: {err}")
            time.sleep(3)


def iniciar_listener_telegram() -> None:
    """
    Inicia o listener de callbacks do Telegram em uma thread daemon de background.
    Garante execução única sem travar a interface gráfica.
    """
    global _listener_running, _listener_thread
    if _listener_running and _listener_thread and _listener_thread.is_alive():
        logger.debug("[TELEGRAM LISTENER] Listener de callbacks já em execução.")
        return

    _listener_running = True
    _listener_thread = threading.Thread(
        target=_loop_listener_telegram,
        name="TelegramCallbackListener",
        daemon=True,
    )
    _listener_thread.start()
    logger.info("🚀 [TELEGRAM LISTENER] Listener de callbacks interativos iniciado em thread daemon.")

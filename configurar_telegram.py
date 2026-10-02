"""
configurar_telegram.py - Diagnóstico e Teste do Bot Telegram (JRBC TECH)
Valida o token, identifica o chat_id e envia alerta de teste.
"""

import os
import requests
from dotenv import load_dotenv

# Carrega o .env
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip('"\'')
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip('"\'')

def obter_info_bot(token: str):
    """Consulta informações do bot no Telegram."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            dados = r.json().get("result", {})
            return dados
        else:
            print(f"❌ Erro ao validar token: HTTP {r.status_code} - {r.text}")
            return None
    except Exception as e:
        print(f"❌ Falha de conexão: {e}")
        return None

def descobrir_chat_id(token: str):
    """Pega o último chat_id de quem enviou mensagem para o bot."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            updates = r.json().get("result", [])
            if updates:
                ultimo = updates[-1]
                msg = ultimo.get("message") or ultimo.get("channel_post", {})
                chat = msg.get("chat", {})
                return str(chat.get("id")), chat.get("first_name", "Usuário")
        return None, None
    except Exception:
        return None, None

def enviar_teste(token: str, chat_id: str):
    """Envia um card de teste para o chat."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": (
            "🚀 **[JRBC TECH - TESTE DE CONECTIVIDADE]**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ **Bot do Telegram:** Operacional\n"
            "📡 **Destino:** Notificações de Serviços e Licitações\n"
            "📍 **Região:** Palmas / Tocantins e Federal\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*O robô já está apto a enviar as oportunidades do SIGA-TO e Compras.gov aqui!*"
        ),
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🌐 Abrir SIGA-TO", "url": "http://siga.to.gov.br/cde/#/fornecedor/abertas"}]
            ]
        }
    }
    r = requests.post(url, json=payload, timeout=10)
    return r.status_code == 200

def main():
    print("\n🔍 Verificando configurações do Telegram no .env...\n")

    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN não encontrado no .env!")
        return

    # 1. Checa o Bot
    bot_info = obter_info_bot(TOKEN)
    if not bot_info:
        return

    nome_bot = bot_info.get("first_name")
    username_bot = bot_info.get("username")
    print(f"🤖 Bot Identificado: {nome_bot} (@{username_bot})")

    # 2. Checa o Chat ID
    alvo_chat_id = CHAT_ID
    if not alvo_chat_id:
        print("⚠️ TELEGRAM_CHAT_ID não está preenchido no .env. Buscando últimas mensagens...")
        detectado, nome = descobrir_chat_id(TOKEN)
        if detectado:
            alvo_chat_id = detectado
            print(f"✅ Chat ID detectado de {nome}: {alvo_chat_id}")
            print(f"👉 Dica: Adicione no seu .env: TELEGRAM_CHAT_ID={alvo_chat_id}")
        else:
            print("❌ Nenhuma mensagem recebida pelo bot ainda.")
            print(f"👉 Abra o Telegram, procure @{username_bot} e envie /start")
            return
    else:
        print(f"📱 Chat ID configurado no .env: {alvo_chat_id}")

    # 3. Envia o alerta de teste
    print("📨 Enviando mensagem de teste...")
    if enviar_teste(TOKEN, alvo_chat_id):
        print("🎉 SUCESSO! A mensagem de teste chegou no seu Telegram.")
    else:
        print("❌ Falha ao enviar mensagem. Verifique se você já iniciou conversa com o bot.")

if __name__ == "__main__":
    main()

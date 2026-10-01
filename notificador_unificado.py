"""
Módulo: notificador_unificado.py
Envia cards no Telegram categorizados por Esfera (Federal, Estadual ou Municipal).
"""
import os
import html
import requests
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def enviar_alerta_executivo(dados: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    esfera = dados.get("esfera", "FEDERAL")
    if esfera == "ESTADUAL":
        badge = "🌲 **[ESTADUAL - GOVERNO DO TOCANTINS / SIGA-TO]**"
    elif esfera == "MUNICIPAL":
        badge = f"🏛 **[MUNICIPAL - TOCANTINS ({dados.get('distancia_palmas', 0)} km de Palmas)]**"
    else:
        badge = "🇧🇷 **[FEDERAL / NACIONAL - PNCP]**"

    orgao = html.escape(str(dados.get("orgao_nome", "Órgão Público")))
    modalidade = html.escape(str(dados.get("modalidade", "Dispensa")))
    numero = html.escape(str(dados.get("numero_processo") or dados.get("numero_pregao") or "S/N"))
    objeto = html.escape(str(dados.get("objeto", "Sem descrição"))[:320])
    valor = float(dados.get("valor_estimado", 0.0))
    link = dados.get("link_edital", "https://pncp.gov.br")

    piso_seguro = float(dados.get("piso_seguro", valor * 0.70 if valor > 0 else 0.0))
    prioridade = html.escape(str(dados.get("prioridade", "NORMAL")))

    texto = (
        f"{badge}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 **Órgão:** {orgao.upper()}\n"
        f"📋 **{modalidade} nº {numero}**\n"
        f"🎯 **Prioridade:** {prioridade}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 **OBJETO DA CONTRATAÇÃO:**\n"
        f"*{objeto}...*\n\n"
        "💰 **FINANCEIRO JRBC TECH:**\n"
        f"• **Valor Estimado:** R$ {valor:,.2f}\n"
        f"• **Piso de Lance (Margem Segura):** R$ {piso_seguro:,.2f}\n\n"
        f"🏷 #{dados.get('uf', 'TO')} #{esfera} #TI #DISPENSA\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "📄 Acessar Certame Oficial", "url": link}]
            ]
        }
    }
    try:
        r = requests.post(url_api, json=body, timeout=12)
        return r.status_code == 200
    except Exception:
        return False

"""
Módulo: buscador_pncp.py
Finalidade: Rastreio nacional no PNCP, persistência no Supabase e alertas no Telegram.
JRBC TECH - CNPJ: 65.488.003/0001-61
"""

import os
import sys
import time
import json
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BuscadorPNCP")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Conexão com Supabase validada.")
    except Exception as e:
        logger.error(f"Falha ao conectar no Supabase: {e}")
else:
    logger.warning("SUPABASE_KEY ausente. Os dados não serão salvos no banco.")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "certames_notificados.json"

HEADERS_PNCP = {
    "User-Agent": "JRBC-Tech-Licitacoes/2.0 (suporte@jrbctech.com.br)",
    "Accept": "application/json"
}

TERMOS_CNAE = [
    "manutenção de computadores",
    "manutenção de microcomputadores",
    "suporte técnico de ti",
    "help desk",
    "service desk",
    "field service",
    "cabeamento estruturado",
    "instalação de rede lógica",
    "locação de computadores",
    "locação de impressoras",
    "recarga de cartuchos",
    "toners e cartuchos",
    "peças de informática"
]

def carregar_historico_notificados() -> set:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return set()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return set(dados.get("notificados", []))
    except Exception:
        return set()

def salvar_historico_notificados(notificados: set):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "notificados": list(notificados),
                "ultima_atualizacao": datetime.now().isoformat()
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Falha ao salvar cache de notificações: {e}")

def formatar_link_pncp(cnpj: str, ano: str, sequencial: str) -> str:
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    if cnpj_limpo and ano and sequencial:
        return f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{ano}/{sequencial}"
    return "https://pncp.gov.br"

def enviar_alerta_telegram(payload: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    orgao = html.escape(str(payload.get("orgao_nome") or "Órgão Público"))
    uf = str(payload.get("uf") or "BR").upper()
    municipio = html.escape(str(payload.get("municipio") or ""))
    localidade = f"{municipio} - {uf}" if municipio and municipio != "Nacional" else uf
    uasg = html.escape(str(payload.get("numero_uasg") or "S/ UASG"))
    modalidade = html.escape(str(payload.get("modalidade") or "Licitação"))
    num_processo = html.escape(str(payload.get("numero_pregao") or "S/N"))
    
    objeto_raw = str(payload.get("objeto") or "Sem descrição")
    objeto = html.escape(objeto_raw[:350])
    
    valor_total = float(payload.get("valor_estimado") or 0.0)
    link_edital = payload.get("link_edital") or "https://pncp.gov.br"

    tipo = payload.get("tipo_contratacao", "SERVICO")
    eh_servico = (tipo == "SERVICO")
    icone = "🛠" if eh_servico else "📦"
    margem = 50.0 if eh_servico else 30.0
    fator_piso = 0.50 if eh_servico else 0.70
    piso = valor_total * fator_piso if valor_total > 0 else 0.0
    tag_tipo = "#SERVICO" if eh_servico else "#PRODUTO"
    tag_mod = "#DISPENSA" if "dispensa" in modalidade.lower() else "#PREGAO"
    
    tag_uf = "#TOCANTINS" if uf == "TO" else "#GOIAS" if uf == "GO" else f"#{uf}"

    data_abertura = payload.get("data_abertura") or ""
    if "T" in str(data_abertura):
        try:
            dt = datetime.fromisoformat(str(data_abertura).replace("Z", "+00:00"))
            data_abertura_fmt = dt.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            data_abertura_fmt = html.escape(str(data_abertura))
    else:
        data_abertura_fmt = html.escape(str(data_abertura or "Consulte o edital"))

    texto = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 **{orgao.upper()}**\n"
        f"📍 {localidade} | **UASG:** {uasg}\n"
        f"📋 **{modalidade} nº {num_processo}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 **OBJETO DA CONTRATAÇÃO:**\n"
        f"*{objeto}...*\n\n"
        "💰 **ANÁLISE DE MARGEM (JRBC TECH):**\n"
        f"• **Valor Estimado Edital:** R$ {valor_total:,.2f}\n"
        f"• **Classificação:** {icone} {tipo}\n"
        f"• **Margem Alvo Líquida:** {margem:.0f}%\n"
        f"• **Piso de Lance Seguro:** R$ {piso:,.2f}\n\n"
        f"📅 **ABERTURA DA SESSÃO:**\n{data_abertura_fmt}\n\n"
        f"🏷 {tag_uf} {tag_mod} #TI {tag_tipo}\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "📄 Abrir no PNCP", "url": link_edital},
                    {"text": "🏛 Portal Compras.gov", "url": "https://www.gov.br/compras"}
                ]
            ]
        }
    }
    try:
        r = requests.post(url_api, json=body, timeout=12)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Erro ao enviar Telegram: {e}")
        return False

def buscar_contratacoes_pncp(termo: str, modalidade: int = 8) -> list[dict]:
    url = "https://pncp.gov.br/api/search/"
    params = {
        "q": termo,
        "tipos_documento": "edital",
        "modalidade": str(modalidade),
        "ordenacao": "-data",
        "pagina": 1,
        "tam_pagina": 20,
        "status": "aberto"
    }

    for tentativa in range(3):
        try:
            res = requests.get(url, params=params, headers=HEADERS_PNCP, timeout=30)
            if res.status_code == 200:
                dados = res.json()
                return dados.get("items", []) or dados.get("data", [])
            elif res.status_code == 429:
                logger.warning("Rate limit no PNCP. Aguardando 4s...")
                time.sleep(4)
            else:
                logger.warning(f"PNCP status {res.status_code} para '{termo}'.")
                time.sleep(2)
        except requests.RequestException as e:
            logger.error(f"Falha de rede ao buscar '{termo}': {e}")
            time.sleep(2)
    return []

def processar_e_salvar_edital(edital: dict, termo: str, modalidade_nome: str, notificados: set):
    cnpj = edital.get("orgao_cnpj") or edital.get("cnpj") or edital.get("orgaoEntidade", {}).get("cnpj", "")
    ano = edital.get("anoCompra") or edital.get("ano", datetime.now().year)
    seq = edital.get("sequencialCompra") or edital.get("sequencial") or edital.get("numero_sequencial", "1")
    num_compra = edital.get("numeroCompra") or edital.get("numero")
    
    numero_controle = edital.get("numero_controle_pncp") or f"{cnpj}-{ano}-{seq}"
    codigo_unidade = edital.get("unidadeOrgao", {}).get("codigoUnidade") or "S/ UASG"
    numero_compra_fmt = f"{num_compra}/{ano}" if num_compra else "S/N"
    data_abertura = edital.get("dataAberturaProposta") or edital.get("dataEncerramentoProposta") or None
    
    link_edital = edital.get("linkSistemaOrigem") or formatar_link_pncp(cnpj, str(ano), str(seq))
    tipo = "PRODUTO" if any(p in termo.lower() for p in ["toner", "peça", "cartucho", "suprimento"]) else "SERVICO"

    payload = {
        "numero_controle_pncp": numero_controle,
        "numero_uasg": codigo_unidade,
        "numero_pregao": numero_compra_fmt,
        "objeto": edital.get("objeto") or edital.get("description", "Sem descrição"),
        "orgao_nome": edital.get("orgao_nome") or edital.get("orgao_razao_social", "Órgão Público"),
        "uf": edital.get("uf", "BR"),
        "municipio": edital.get("municipio_nome", "Nacional"),
        "valor_estimado": edital.get("valor_global") or edital.get("valor_total_estimado", 0.0),
        "data_abertura": data_abertura,
        "link_edital": link_edital,
        "termo_busca": termo,
        "modalidade": modalidade_nome,
        "tipo_contratacao": tipo,
        "status_analise": "pendente",
        "atualizado_em": datetime.now(timezone.utc).isoformat()
    }

    # 1. Salva no Supabase
    if supabase:
        try:
            supabase.table("licitacoes").upsert(
                payload,
                on_conflict="numero_controle_pncp"
            ).execute()
            logger.info(f"[{payload['uf']}] {modalidade_nome} salva: {payload['orgao_nome'][:30]}")
        except Exception as e:
            logger.error(f"Erro ao salvar no Supabase: {e}")

    # 2. Envia para o Telegram (apenas inéditos)
    if numero_controle not in notificados:
        if enviar_alerta_telegram(payload):
            logger.info(f"🔔 Alerta Telegram enviado: [{payload['uf']}] {numero_controle}")
            notificados.add(numero_controle)
            salvar_historico_notificados(notificados)
            time.sleep(1)

def executar_varredura_completa():
    logger.info("=== INICIANDO VARREDURA NACIONAL PNCP (JRBC TECH) ===")
    notificados = carregar_historico_notificados()
    
    # Dispensas (Modalidade 8)
    for termo in TERMOS_CNAE:
        logger.info(f"Buscando Dispensa: '{termo}'...")
        editais = buscar_contratacoes_pncp(termo, modalidade=8)
        for ed in editais:
            processar_e_salvar_edital(ed, termo, "Dispensa Eletrônica", notificados)
        time.sleep(1.5)

    # Pregões (Modalidade 6)
    for termo in TERMOS_CNAE[:5]:
        logger.info(f"Buscando Pregão: '{termo}'...")
        editais = buscar_contratacoes_pncp(termo, modalidade=6)
        for ed in editais:
            processar_e_salvar_edital(ed, termo, "Pregão Eletrônico", notificados)
        time.sleep(1.5)

    logger.info("=== VARREDURA FINALIZADA COM SUCESSO ===")

if __name__ == "__main__":
    executar_varredura_completa()

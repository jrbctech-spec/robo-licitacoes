"""
Módulo: buscador_pncp.py
Finalidade: Rastreio nacional no PNCP, persistência no Supabase e alertas no Telegram.
JRBC TECH - CNPJ: 65.488.003/0001-61
"""

import os
import sys
import re
import time
import json
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from radar_telegram import validar_edital_jrbc, TERMOS_BLOQUEADOS

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

def formatar_link_pncp(item: dict) -> str:
    # 1. Se o órgão informou o link direto da sala (ex: Compras.gov.br), usa direto
    link_origem = item.get("linkSistemaOrigem")
    if isinstance(link_origem, str) and "http" in link_origem:
        return link_origem

    # 2. Tenta extrair diretamente dos campos da API do PNCP
    cnpj = item.get("cnpjOrgao") or item.get("orgaoEntidade", {}).get("cnpj") or item.get("orgao_cnpj") or item.get("cnpj")
    ano = item.get("anoCompra") or item.get("ano")
    seq = item.get("sequencialCompra") or item.get("sequencial")
    
    if cnpj and ano and seq:
        try:
            return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(seq)}"
        except Exception:
            return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"

    # 3. Fallback via regex no numeroControlePNCP (ex: 26063838000118-1-000129/2026)
    num_controle = item.get("numeroControlePNCP") or item.get("numero_controle_pncp") or ""
    m = re.match(r"(\d+)-\d+-(\d+)/(\d+)", str(num_controle))
    if m:
        cnpj_extraido = m.group(1)
        seq_extraido = int(m.group(2))
        ano_extraido = m.group(3)
        return f"https://pncp.gov.br/app/editais/{cnpj_extraido}/{ano_extraido}/{seq_extraido}"

    return "https://pncp.gov.br"

from motor_assertivo import avaliar_certame
from notificador_unificado import enviar_alerta_executivo
from coletor_nucleogov import varrer_municipios_nucleogov
from siga_monitor import varrer_siga_to
from radar_telegram import validar_edital_jrbc

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

    for tentativa in range(5):
        try:
            res = requests.get(url, params=params, headers=HEADERS_PNCP, timeout=30)
            if res.status_code == 200:
                dados = res.json()
                return dados.get("items", []) or dados.get("data", [])
            elif res.status_code == 429:
                espera = 4 * (tentativa + 1)
                logger.warning(f"Rate limit no PNCP. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                logger.warning(f"PNCP status {res.status_code} para '{termo}'.")
                time.sleep(2 * (tentativa + 1))
        except requests.RequestException as e:
            espera = 3 * (tentativa + 1)
            logger.error(f"Falha de rede ao buscar '{termo}': {e}. Tentando novamente em {espera}s...")
            time.sleep(espera)
    return []

def processar_e_salvar_edital(edital: dict, termo: str, modalidade_nome: str, notificados: set):
    objeto = edital.get("objeto") or edital.get("description") or edital.get("objetoCompra") or ""
    if any(b in objeto.lower() for b in TERMOS_BLOQUEADOS) or not validar_edital_jrbc(objeto):
        logger.debug(f"[CNAE DESCARTE] Objeto fora dos CNAEs JRBC TECH: {objeto[:80]}")
        return

    cnpj = edital.get("orgao_cnpj") or edital.get("cnpj") or edital.get("orgaoEntidade", {}).get("cnpj", "")
    ano = edital.get("anoCompra") or edital.get("ano", datetime.now().year)
    seq = edital.get("sequencialCompra") or edital.get("sequencial") or edital.get("numero_sequencial", "1")
    num_compra = edital.get("numeroCompra") or edital.get("numero")
    
    numero_controle = edital.get("numero_controle_pncp") or f"{cnpj}-{ano}-{seq}"
    codigo_unidade = edital.get("unidadeOrgao", {}).get("codigoUnidade") or "S/ UASG"
    numero_compra_fmt = f"{num_compra}/{ano}" if num_compra else "S/N"
    
    data_abertura = (
        edital.get("dataAberturaProposta")
        or edital.get("data_inicio_recebimento_propostas")
        or edital.get("dataPublicacaoPncp")
        or edital.get("data_publicacao_pncp")
        or None
    )
    data_encerramento = (
        edital.get("dataEncerramentoProposta")
        or edital.get("data_fim_recebimento_propostas")
        or edital.get("dataAberturaProposta")
        or None
    )
    
    link_edital = formatar_link_pncp(edital)
    url_sala = edital.get("linkSistemaOrigem") or link_edital
    tipo = "PRODUTO" if any(p in termo.lower() for p in ["toner", "peça", "cartucho", "suprimento"]) else "SERVICO"

    payload = {
        "numero_controle_pncp": numero_controle,
        "numero_uasg": codigo_unidade,
        "numero_pregao": numero_compra_fmt,
        "objeto": objeto,
        "orgao_nome": edital.get("orgao_nome") or edital.get("orgao_razao_social", "Órgão Público"),
        "uf": edital.get("uf", "BR"),
        "municipio": edital.get("municipio_nome", "Nacional"),
        "valor_estimado": edital.get("valor_global") or edital.get("valor_total_estimado", 0.0),
        "data_abertura": data_abertura,
        "data_encerramento": data_encerramento,
        "link_edital": link_edital,
        "url_sala_disputa": url_sala,
        "termo_busca": termo,
        "modalidade": modalidade_nome,
        "tipo_contratacao": tipo,
        "status_analise": "pendente",
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "portal": "COMPRAS_GOV"
    }

    # --- Avaliação Geográfica ---
    crivo = avaliar_certame(payload)
    if not crivo.get("aprovado"):
        logger.debug(crivo.get("motivo"))
        return
        
    payload["prioridade"] = crivo.get("prioridade")
    # payload["piso_seguro"] = float(crivo.get("piso_seguro", 0.0))
    # payload["lucro_estimado"] = float(crivo.get("lucro_estimado", 0.0))

    # 1. Salva no Supabase
    lic_id = None
    if supabase:
        try:
            res_up = supabase.table("licitacoes").upsert(
                payload,
                on_conflict="numero_controle_pncp"
            ).execute()
            if res_up.data and isinstance(res_up.data, list) and len(res_up.data) > 0:
                primeiro = res_up.data[0]
                if isinstance(primeiro, dict):
                    lic_id = primeiro.get("id")
            logger.info(f"[{payload['uf']}] {modalidade_nome} salva: {payload['orgao_nome'][:30]}")
        except Exception as e:
            logger.error(f"Erro ao salvar no Supabase: {e}")

    # Sincroniza itens essenciais se tivermos o ID
    if lic_id and cnpj and ano and seq:
        try:
            from pncp_client import sincronizar_itens_certame_supabase
            sincronizar_itens_certame_supabase(cnpj, str(ano), str(seq), str(lic_id))
        except Exception as e:
            logger.warning(f"Aviso ao sincronizar itens do PNCP: {e}")

    # 2. Envia para o Telegram (apenas inéditos)
    if numero_controle not in notificados:
        if enviar_alerta_executivo(payload):
            logger.info(f"🔔 Alerta Telegram enviado: [{payload['uf']}] {numero_controle}")
            notificados.add(numero_controle)
            salvar_historico_notificados(notificados)
            time.sleep(1)

def processar_outras_fontes(oportunidades, notificados):
    for item in oportunidades:
        id_unico = item.get("id_unico")
        if not id_unico: continue
        
        objeto = item.get("objeto") or item.get("objetoCompra") or ""
        if any(b in objeto.lower() for b in TERMOS_BLOQUEADOS) or not validar_edital_jrbc(objeto):
            continue
        
        payload = {
            "numero_controle_pncp": id_unico,
            "numero_uasg": "S/ UASG",
            "numero_pregao": item.get("numero_processo"),
            "objeto": objeto,
            "orgao_nome": item.get("orgao_nome"),
            "uf": item.get("uf"),
            "municipio": item.get("municipio", ""),
            "valor_estimado": item.get("valor_estimado"),
            "data_abertura": item.get("data_abertura"),
            "data_encerramento": item.get("data_encerramento") or item.get("data_fim") or item.get("data_fim_proposta"),
            "link_edital": item.get("link_edital"),
            "termo_busca": "TI",
            "modalidade": item.get("modalidade"),
            "tipo_contratacao": "SERVICO",
            "status": "pendente",
            "status_analise": "NOVA",
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
            "portal": "COMPRAS_GOV"
        }
        
        # --- Avaliação Geográfica ---
        crivo = avaliar_certame(payload)
        if not crivo.get("aprovado"):
            logger.debug(crivo.get("motivo"))
            continue
            
        payload["prioridade"] = crivo.get("prioridade")
        # payload["piso_seguro"] = float(crivo.get("piso_seguro", 0.0))
        # payload["lucro_estimado"] = float(crivo.get("lucro_estimado", 0.0))
        
        # Inject for Telegram
        item["prioridade"] = payload["prioridade"]
        # item["piso_seguro"] = payload["piso_seguro"]
        item["portal"] = "COMPRAS_GOV"

        if supabase:
            try:
                supabase.table("licitacoes").upsert(
                    payload,
                    on_conflict="numero_controle_pncp"
                ).execute()
            except Exception as e:
                erro_str = str(e)
                if "PGRST204" in erro_str or "Could not find the" in erro_str:
                    logger.warning(f"Schema mismatch detectado no Supabase (PGRST204). Tentando salvar modo de compatibilidade...")
                    payload.pop("prioridade", None)
                    payload.pop("portal", None)
                    try:
                        supabase.table("licitacoes").upsert(
                            payload,
                            on_conflict="numero_controle_pncp"
                        ).execute()
                        logger.info("Salvo com sucesso em modo de compatibilidade.")
                    except Exception as fallback_e:
                        logger.error(f"Erro no fallback do Supabase: {fallback_e}")
                else:
                    logger.error(f"Erro ao salvar no Supabase (Fonte Externa): {e}")

        if id_unico not in notificados:
            # Passa o item original para ter 'esfera' e 'distancia_palmas'
            if enviar_alerta_executivo(item):
                logger.info(f"🔔 Alerta Telegram enviado ({item.get('origem')}): {id_unico}")
                notificados.add(id_unico)
                salvar_historico_notificados(notificados)
                time.sleep(1)


def executar_varredura_tocantins():
    logger.info("=== START: Varredura Exclusiva Tocantins (TO) ===")
    notificados = carregar_historico_notificados()
    try:
        from pncp_client import buscar_publicacoes_pncp
        for mod in [6, 8]:
            res_to = buscar_publicacoes_pncp(uf="TO", codigo_modalidade=mod, tamanho_pagina=50)
            itens_to = res_to.get("data", [])
            for item in itens_to:
                objeto = item.get("objetoCompra") or item.get("objeto") or ""
                if not validar_edital_jrbc(objeto):
                    continue
                processar_e_salvar_edital(item, "TI-TO", "Pregão Eletrônico" if mod == 6 else "Dispensa Eletrônica", notificados)
            time.sleep(1.5)
    except Exception as e:
        logger.error(f"Erro ao varrer PNCP TO: {e}")
        
    logger.info(">>> Varrendo NúcleoGov (Municípios do TO)...")
    op_nucleo = varrer_municipios_nucleogov()
    processar_outras_fontes(op_nucleo, notificados)
    
    logger.info(">>> Varrendo SIGA-TO (Governo do TO)...")
    op_siga = varrer_siga_to()
    processar_outras_fontes(op_siga, notificados)
    logger.info("=== FIM: Varredura Tocantins ===")

def executar_varredura_completa():
    logger.info("=== INICIANDO RADAR TRÍPLICE (JRBC TECH) ===")
    notificados = carregar_historico_notificados()
    
    logger.info(">>> 1. Varrendo PNCP (Federal/Nacional)...")
    for termo in TERMOS_CNAE:
        editais = buscar_contratacoes_pncp(termo, modalidade=8)
        for ed in editais:
            objeto = ed.get("objetoCompra") or ed.get("objeto") or ed.get("description") or ""
            if not validar_edital_jrbc(objeto):
                continue
            processar_e_salvar_edital(ed, termo, "Dispensa Eletrônica", notificados)
        time.sleep(1.5)

    for termo in TERMOS_CNAE[:5]:
        editais = buscar_contratacoes_pncp(termo, modalidade=6)
        for ed in editais:
            objeto = ed.get("objetoCompra") or ed.get("objeto") or ed.get("description") or ""
            if not validar_edital_jrbc(objeto):
                continue
            processar_e_salvar_edital(ed, termo, "Pregão Eletrônico", notificados)
        time.sleep(1.5)

    logger.info(">>> 1.5. Varrendo PNCP (Filtro Específico Tocantins - TO)...")
    try:
        from pncp_client import buscar_publicacoes_pncp
        for mod in [6, 8]:
            res_to = buscar_publicacoes_pncp(uf="TO", codigo_modalidade=mod, tamanho_pagina=50)
            itens_to = res_to.get("data", [])
            for item in itens_to:
                objeto = item.get("objetoCompra") or item.get("objeto") or ""
                if not validar_edital_jrbc(objeto):
                    continue
                processar_e_salvar_edital(item, "TI-TO", "Pregão Eletrônico" if mod == 6 else "Dispensa Eletrônica", notificados)
            time.sleep(1.5)
    except Exception as e:
        logger.error(f"Erro ao varrer PNCP TO: {e}")

    logger.info(">>> 2. Varrendo NúcleoGov (Municípios do TO)...")
    op_nucleo = varrer_municipios_nucleogov()
    processar_outras_fontes(op_nucleo, notificados)
    
    logger.info(">>> 3. Varrendo SIGA-TO (Governo do TO)...")
    op_siga = varrer_siga_to()
    processar_outras_fontes(op_siga, notificados)

    logger.info("=== RADAR TRÍPLICE FINALIZADO COM SUCESSO ===")

if __name__ == "__main__":
    executar_varredura_completa()

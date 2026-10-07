"""
coletor_diario.py - Radar Diário Automático
Coleta certames do dia (PNCP + SIGA-TO), salva no Supabase e envia resumo no Telegram.
"""
import os
import sys
import time
from datetime import datetime, timedelta
import logging
from pncp_client import buscar_publicacoes_pncp
from siga_to_monitor import SigaMonitor
from supabase_client import inserir_licitacao
from radar_telegram import validar_edital_jrbc, TERMOS_BLOQUEADOS, formatar_link_pncp
import requests
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ColetorDiario")

def notificar_telegram(total_pncp, total_siga):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
        
    total = total_pncp + total_siga
    mensagem = (
        "🔍 **[RESUMO DO RADAR DIÁRIO]** 🔍\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Total Encontrado: {total} certame(s) novo(s)\n"
        f"🌐 PNCP: {total_pncp}\n"
        f"🌴 SIGA-TO: {total_siga}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Erro ao notificar telegram: {e}")

async def varredura_siga():
    try:
        monitor = SigaMonitor()
        return await monitor.executar_varredura()
    except Exception as e:
        logger.error(f"Erro na varredura SIGA-TO: {e}")
        return 0

def varredura_pncp():
    hoje = datetime.now()
    d_inicial = hoje.strftime("%Y%m%d")
    d_final = (hoje + timedelta(days=5)).strftime("%Y%m%d")
    
    novas = 0
    resultados = buscar_publicacoes_pncp(data_inicial=d_inicial, data_final=d_final, codigo_modalidade=None)
    itens = resultados.get("data", [])
    
    for item in itens:
        objeto = str(item.get("objetoCompra", "") or "")
        if any(b in objeto.lower() for b in TERMOS_BLOQUEADOS) or not validar_edital_jrbc(objeto):
            continue

        try:
            cnpj = item.get("orgaoEntidade", {}).get("cnpj")
            ano = item.get("anoCompra")
            seq = item.get("sequencialCompra")
            
            if cnpj and ano and seq:
                url = formatar_link_pncp(item)
                
                data_abertura = item.get("dataAberturaProposta") or item.get("dataPublicacaoPncp")
                data_encerramento = item.get("dataEncerramentoProposta") or item.get("dataAberturaProposta")
                valor_est = float(item.get("valorTotalEstimado") or 0.0)
                
                dados = {
                    "numero_controle_pncp": f"{cnpj}-{ano}-{seq}",
                    "numero_uasg": str(item.get("unidadeOrgao", {}).get("codigoUnidade", "PNCP")),
                    "numero_pregao": f"{seq}/{ano}",
                    "orgao": item.get("orgaoEntidade", {}).get("razaoSocial", ""),
                    "orgao_nome": item.get("orgaoEntidade", {}).get("razaoSocial", ""),
                    "objeto": objeto[:2000],
                    "modalidade": item.get("modalidadeNome", "PNCP"),
                    "uf": item.get("unidadeOrgao", {}).get("ufSigla", ""),
                    "link_sistema": url,
                    "link_edital": url,
                    "valor_estimado": valor_est,
                    "status": "pendente",
                    "status_analise": "NOVA",
                    "portal": "COMPRAS_GOV",
                    "prioridade": "MEDIA",
                    "atualizado_em": datetime.now().isoformat(),
                }
                if data_abertura:
                    dados["data_abertura"] = data_abertura
                if data_encerramento:
                    dados["data_encerramento"] = data_encerramento
                    
                from supabase_client import get_client
                sb = get_client()
                sb.table("licitacoes").upsert(dados, on_conflict="numero_controle_pncp").execute()
                novas += 1
        except Exception as e:
            logger.error(f"Erro ao salvar PNCP no banco: {e}")
                
    return novas

def executar_radar_diario():
    logger.info("Iniciando Radar Diário...")
    total_pncp = varredura_pncp()
    
    total_siga = asyncio.run(varredura_siga())
    
    notificar_telegram(total_pncp, total_siga)
    logger.info(f"Radar Concluído! PNCP: {total_pncp}, SIGA-TO: {total_siga}")
    return total_pncp, total_siga

if __name__ == "__main__":
    executar_radar_diario()

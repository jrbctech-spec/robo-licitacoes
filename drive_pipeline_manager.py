import os
import shutil
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

# Base path for Google Drive (User can map this or use Desktop Sync)
DRIVE_BASE_DIR = Path(os.getenv("DRIVE_BASE_DIR", os.path.expanduser("~/Google Drive/Licitacoes_Funil")))

FUNNEL_FOLDERS = {
    "01_VERIFICAR": "01 - [AZUL] Oportunidades para Verificar",
    "02_AGUARDANDO": "02 - [AMARELO] Aguardando Abertura",
    "03_DISPUTA": "03 - [ROXO] Concorrendo em Disputa",
    "04_VENCIDAS": "04 - [VERDE] Ganhamos (Vencidas)",
    "05_DESCARTADAS": "05 - [VERMELHO] Descartadas (Lixeira 3 Dias)"
}

def garantir_estrutura_funil():
    """Garante que a estrutura de pastas do funil existe."""
    DRIVE_BASE_DIR.mkdir(parents=True, exist_ok=True)
    for folder_name in FUNNEL_FOLDERS.values():
        (DRIVE_BASE_DIR / folder_name).mkdir(exist_ok=True)
        
def formatar_nome_pasta(licitacao: dict) -> str:
    portal = licitacao.get("portal", "S/N")
    if portal == "COMPRAS_GOV":
        prefixo = licitacao.get("numero_uasg", "S_UASG")
    else:
        prefixo = "SIGA"
        
    num_pregao = str(licitacao.get("numero_pregao", "S_N")).replace("/", "-").replace("\\", "-")
    orgao = str(licitacao.get("orgao_nome", "Orgao Desconhecido"))[:30].strip().replace("/", "-")
    return f"[{prefixo}] - [{num_pregao}] - [{orgao}]"

def determinar_pasta_status(status: str) -> str:
    status = (status or "pendente").lower()
    if "ganha" in status or "vencida" in status:
        return FUNNEL_FOLDERS["04_VENCIDAS"]
    elif "disputa" in status or "concorrendo" in status:
        return FUNNEL_FOLDERS["03_DISPUTA"]
    elif "aguardando" in status or "agendada" in status:
        return FUNNEL_FOLDERS["02_AGUARDANDO"]
    elif "descartada" in status:
        return FUNNEL_FOLDERS["05_DESCARTADAS"]
    else:
        return FUNNEL_FOLDERS["01_VERIFICAR"]

def criar_resumo_processo(caminho_pasta: Path, licitacao: dict):
    resumo_path = caminho_pasta / "resumo_processo.txt"
    with open(resumo_path, "w", encoding="utf-8") as f:
        f.write(f"=== RESUMO DO PROCESSO ===\n")
        f.write(f"Órgão: {licitacao.get('orgao_nome')}\n")
        f.write(f"Número: {licitacao.get('numero_pregao')}\n")
        f.write(f"Objeto: {licitacao.get('objeto')}\n")
        f.write(f"Valor Estimado: R$ {licitacao.get('valor_estimado', 0.0)}\n")
        f.write(f"Link: {licitacao.get('link_edital')}\n")
        f.write(f"Status: {licitacao.get('status_analise')}\n")
        f.write(f"Prioridade: {licitacao.get('prioridade')}\n")
        f.write(f"Portal: {licitacao.get('portal')}\n")

def sincronizar_pastas_drive(supabase) -> int:
    """Sincroniza os processos do Supabase com o Drive."""
    garantir_estrutura_funil()
    
    try:
        res = supabase.table("licitacoes").select("*").execute()
        licitacoes = res.data or []
    except Exception as e:
        logger.error(f"Erro ao buscar licitações no Supabase para o Drive: {e}")
        return 0
        
    pastas_criadas = 0
    for licitacao in licitacoes:
        pasta_status = determinar_pasta_status(licitacao.get("status_analise"))
        nome_pasta = formatar_nome_pasta(licitacao)
        caminho_pasta = DRIVE_BASE_DIR / pasta_status / nome_pasta
        
        # Move de outra pasta se o status mudou (opcional, requer varrer as outras)
        # Para simplificar: cria se não existir na pasta correta
        if not caminho_pasta.exists():
            caminho_pasta.mkdir(parents=True, exist_ok=True)
            pastas_criadas += 1
            
        criar_resumo_processo(caminho_pasta, licitacao)
        
    return pastas_criadas

def rotina_limpeza_automatica(supabase):
    """Limpa descartados há mais de 3 dias no Supabase e no Drive."""
    hoje = datetime.now(timezone.utc)
    limite = hoje - timedelta(days=3)
    
    # 1. Limpar Drive
    pasta_descartadas = DRIVE_BASE_DIR / FUNNEL_FOLDERS["05_DESCARTADAS"]
    if pasta_descartadas.exists():
        for subpasta in pasta_descartadas.iterdir():
            if subpasta.is_dir():
                mtime = datetime.fromtimestamp(subpasta.stat().st_mtime, tz=timezone.utc)
                if mtime < limite:
                    try:
                        shutil.rmtree(subpasta)
                        logger.info(f"Limpeza Drive: {subpasta.name} excluída (>3 dias).")
                    except Exception as e:
                        logger.error(f"Erro ao excluir pasta {subpasta}: {e}")
                        
    # 2. Limpar Supabase
    limite_iso = limite.isoformat()
    try:
        # Apaga os registros com status descartada modificados/criados há mais de 3 dias
        # Precisamos de um campo de data. Geralmente atualizado_em
        supabase.table("licitacoes").delete().eq("status_analise", "descartada").lt("atualizado_em", limite_iso).execute()
        logger.info("Limpeza Supabase: Descartadas há mais de 3 dias excluídas.")
    except Exception as e:
        logger.error(f"Erro ao limpar Supabase: {e}")

def executar_pipeline_drive(supabase):
    try:
        qtd = sincronizar_pastas_drive(supabase)
        rotina_limpeza_automatica(supabase)
        return qtd
    except Exception as e:
        logger.error(f"Falha na Pipeline do Drive: {e}")
        return 0

"""
Módulo: buscador_pncp.py
Objetivo: Rastrear editais em todo o Brasil para serviços de TI e Help Desk/Field Service.
"""

import os
import time
import logging
from datetime import datetime, date
import requests
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Pega da variável de ambiente OU usa a URL padrão oficial da JRBC TECH
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://szphoxostihnpcovyzvx.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Termos-chave de TI para pesquisa
TERMOS_BUSCA_CCMEI = [
    # Manutenção e Field Service (CNAE 9511-8/00 e 9512-6/00)
    "manutencao preventiva e corretiva de computadores",
    "manutencao de microcomputadores",
    "suporte tecnico em informatica",
    "reparacao de equipamentos de informatica",
    "manutencao de impressoras",
    
    # Redes e Telecom (CNAE 6190-6/99)
    "instalacao de rede estruturada",
    "cabeamento estruturado",
    "instalacao de pontos de rede",
    
    # Locação (CNAE 7733-1/00)
    "locacao de computadores",
    "locacao de equipamentos de informatica",
    "aluguel de impressoras",
    
    # Suprimentos e Comércio (CNAE 4751-2/01 e 4751-2/02)
    "fornecimento de pecas de informatica",
    "recarga de cartuchos",
    "aquisicao de toners e cartuchos",
    "suprimentos de informatica"
]

HEADERS = {
    "User-Agent": "JRBC-Tech-Licitacoes/1.0 (contato@jrbctech.com.br)",
    "Accept": "application/json"
}

def montar_link_pncp(item: dict) -> str:
    """Gera o link público e direto do edital no portal PNCP."""
    cnpj = item.get("orgao_cnpj") or item.get("cnpj") or ""
    ano = item.get("ano") or ""
    seq = item.get("sequencial") or item.get("numero_sequencial") or ""
    
    # Se o item já trouxer o link estruturado ou número de controle:
    numero_controle = item.get("numero_controle_pncp", "")
    if numero_controle and "-" in numero_controle:
        # Exemplo: 00000000000000-1-000123/2026 -> cnpj/ano/seq
        try:
            partes = numero_controle.split("-")
            cnpj_part = partes[0]
            resto = partes[2].split("/")
            seq_part = int(resto[0])
            ano_part = resto[1]
            return f"https://pncp.gov.br/app/editais/{cnpj_part}/{ano_part}/{seq_part}"
        except Exception:
            pass

    if cnpj and ano and seq:
        return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
    
    return item.get("url_edital") or "https://pncp.gov.br/app/editais"

def buscar_editais_termo(termo: str, pagina: int = 1) -> list[dict]:
    """Consulta a API do PNCP para um termo específico em todo o Brasil."""
    url = "https://pncp.gov.br/api/search/"
    params = {
        "q": termo,
        "tipos_documento": "edital",
        "ordenacao": "-data",
        "pagina": pagina,
        "tam_pagina": 25,
        "status": "aberto"  # Foco em editais com prazo de envio aberto
    }
    
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            dados = response.json()
            return dados.get("items", []) or dados.get("data", [])
        elif response.status_code == 429:
            logging.warning("Rate limit atingido no PNCP. Aguardando 10 segundos...")
            time.sleep(10)
        else:
            logging.error(f"Erro {response.status_code} ao buscar termo '{termo}'")
    except requests.RequestException as e:
        logging.error(f"Falha de conexão na busca de '{termo}': {e}")
    
    return []

def salvar_edital_supabase(item: dict, termo_origem: str):
    """Salva edital no Supabase evitando duplicações pelo número de controle."""
    link_edital = montar_link_pncp(item)
    numero_controle = item.get("numero_controle_pncp") or str(item.get("id"))
    
    payload = {
        "numero_controle_pncp": numero_controle,
        "objeto": item.get("objeto") or item.get("description"),
        "orgao_nome": item.get("orgao_nome") or item.get("orgao_razao_social"),
        "uf": item.get("uf"),
        "municipio": item.get("municipio_nome"),
        "valor_estimado": item.get("valor_global") or item.get("valor_total_estimado"),
        "data_abertura": item.get("data_abertura_proposta") or item.get("data_publicacao"),
        "link_edital": link_edital,
        "termo_busca": termo_origem,
        "status_analise": "pendente",
        "criado_em": datetime.utcnow().isoformat()
    }
    
    try:
        # Upsert baseado em numero_controle_pncp
        supabase.table("licitacoes").upsert(
            payload,
            on_conflict="numero_controle_pncp"
        ).execute()
        logging.info(f"[NOVO/ATUALIZADO] {payload['uf']} - {payload['orgao_nome']}: {link_edital}")
    except Exception as e:
        logging.error(f"Erro ao salvar no Supabase: {e}")

def executar_varredura_nacional():
    """Executa a rotina completa por todas as palavras-chave."""
    logging.info("=== INICIANDO VARREDURA NACIONAL DE EDITAIS DE TI ===")
    total_encontrados = 0
    
    for termo in TERMOS_BUSCA_CCMEI:
        logging.info(f"Pesquisando termo: '{termo}'...")
        itens = buscar_editais_termo(termo)
        for item in itens:
            salvar_edital_supabase(item, termo)
            total_encontrados += 1
        # Delay de cortesia entre chamadas conforme regra do motor
        time.sleep(1.5)
        
    logging.info(f"=== VARREDURA FINALIZADA: {total_encontrados} registros processados ===")

if __name__ == "__main__":
    executar_varredura_nacional()


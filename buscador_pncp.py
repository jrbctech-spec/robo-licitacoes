"""
Módulo: buscador_pncp.py
Finalidade: Coleta contínua de Dispensas e Pregões abertos no PNCP
alinhados aos CNAEs da JRBC TECH e gravação no Supabase.
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

# Configurações Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_KEY:
    logging.warning("SUPABASE_KEY não encontrada. Os dados não serão persistidos no banco.")
    supabase = None
else:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("Conexão com Supabase estabelecida com sucesso.")
    except Exception as e:
        logging.error(f"Falha ao conectar no Supabase: {e}")
        supabase = None

# Cabeçalho padrão para o PNCP
HEADERS = {
    "User-Agent": "JRBC-Tech-Licitacoes/2.0 (suporte@jrbctech.com.br)",
    "Accept": "application/json"
}

# Palavras-chave mapeadas pelo CCMEI (Serviços e Produtos)
TERMOS_CNAE = [
    # CNAE 9511-8/00 e 9512-6/00 - Manutenção e Suporte TI
    "manutenção de computadores",
    "manutenção de microcomputadores",
    "suporte técnico de ti",
    "help desk",
    "service desk",
    "field service",
    
    # CNAE 6190-6/99 - Redes e Telecomunicações
    "cabeamento estruturado",
    "instalação de rede lógica",
    
    # CNAE 7733-1/00 - Locação de Equipamentos
    "locação de computadores",
    "locação de impressoras",
    
    # CNAE 4751-2/01 e 4751-2/02 - Suprimentos e Peças
    "recarga de cartuchos",
    "toners e cartuchos",
    "peças de informática"
]

def formatar_link_pncp(cnpj: str, ano: str, sequencial: str) -> str:
    """Monta a URL pública canônica para abertura no navegador."""
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    return f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{ano}/{sequencial}"

def buscar_contratacoes_pncp(termo: str, modalidade: int = 8) -> list[dict]:
    """
    Consulta o endpoint de pesquisa de editais do PNCP.
    modalidade 8 = Dispensa de Licitação (Lei 14.133)
    modalidade 6 = Pregão Eletrônico
    """
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

    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if res.status_code == 200:
            dados = res.json()
            return dados.get("items", []) or dados.get("data", [])
        elif res.status_code == 429:
            logging.warning("PNCP retornou Rate Limit (HTTP 429). Aguardando 5 segundos...")
            time.sleep(5)
        else:
            logging.error(f"Erro {res.status_code} na consulta do termo '{termo}'")
    except requests.RequestException as e:
        logging.error(f"Exceção de rede no PNCP para '{termo}': {e}")
        
    return []

def extrair_itens_edital(cnpj: str, ano: str, sequencial: str) -> list[dict]:
    """
    Consulta a API de itens da contratação direta conforme Swagger oficial:
    GET /api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{sequencialCompra}/itens
    """
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
    url = f"https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj_limpo}/compras/{ano}/{sequencial}/itens"
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
        if res.status_code == 200:
            return res.json() or []
    except Exception:
        pass
    return []

def salvar_no_supabase(edital: dict, termo: str, modalidade_nome: str):
    """Insere ou atualiza o certame na tabela 'licitacoes'."""
    if not supabase:
        return

    cnpj = edital.get("orgao_cnpj") or edital.get("cnpj", "")
    ano = edital.get("ano", datetime.now().year)
    seq = edital.get("numero_sequencial") or edital.get("sequencial", "1")
    numero_controle = edital.get("numero_controle_pncp") or f"{cnpj}-{ano}-{seq}"
    
    link_edital = formatar_link_pncp(cnpj, ano, seq)
    
    # Classificação inicial do tipo de certame
    tipo = "PRODUTO" if any(p in termo.lower() for p in ["toner", "peça", "cartucho", "suprimento"]) else "SERVICO"

    payload = {
        "numero_controle_pncp": numero_controle,
        "objeto": edital.get("objeto") or edital.get("description", "Sem descrição"),
        "orgao_nome": edital.get("orgao_nome") or edital.get("orgao_razao_social", "Órgão Público"),
        "uf": edital.get("uf", "BR"),
        "municipio": edital.get("municipio_nome", "Nacional"),
        "valor_estimado": edital.get("valor_global") or edital.get("valor_total_estimado", 0.0),
        "data_abertura": edital.get("data_abertura_proposta") or edital.get("data_publicacao"),
        "link_edital": link_edital,
        "termo_busca": termo,
        "modalidade": modalidade_nome,
        "tipo_contratacao": tipo,
        "status_analise": "pendente",
        "atualizado_em": datetime.now(timezone.utc).isoformat()
    }

    try:
        supabase.table("licitacoes").upsert(
            payload,
            on_conflict="numero_controle_pncp"
        ).execute()
        logging.info(f"[{payload['uf']}] {modalidade_nome} gravada: {payload['orgao_nome'][:30]} | {link_edital}")
    except Exception as e:
        logging.error(f"Erro ao salvar licitação no Supabase: {e}")

def executar_varredura_completa():
    """Roda a rotina por todas as categorias e modalidades (Dispensas e Pregões)."""
    logging.info("=== INICIANDO VARREDURA NACIONAL PNCP (JRBC TECH) ===")
    
    # 1. Varre Dispensas de Licitação (Modalidade 8 - Prioridade MEI)
    for termo in TERMOS_CNAE:
        logging.info(f"Buscando Dispensa: '{termo}'...")
        editais = buscar_contratacoes_pncp(termo, modalidade=8)
        for ed in editais:
            salvar_no_supabase(ed, termo, "Dispensa Eletrônica")
        time.sleep(1.2) # Respeito ao rate limit

    # 2. Varre Pregões Eletrônicos (Modalidade 6)
    for termo in TERMOS_CNAE[:4]: # Foco nos principais de serviços de TI
        logging.info(f"Buscando Pregão: '{termo}'...")
        editais = buscar_contratacoes_pncp(termo, modalidade=6)
        for ed in editais:
            salvar_no_supabase(ed, termo, "Pregão Eletrônico")
        time.sleep(1.2)

    logging.info("=== VARREDURA FINALIZADA COM SUCESSO ===")

if __name__ == "__main__":
    executar_varredura_completa()

"""
Módulo: filtro_dispensas.py
Filtra contratações diretas abertas em todo o Brasil.
"""
from datetime import datetime, timezone
import requests

def buscar_dispensas_abertas(termo: str) -> list[dict]:
    """Consulta apenas Dispensas Eletrônicas (modalidade 8) com prazo aberto."""
    url = "https://pncp.gov.br/api/search/"
    params = {
        "q": termo,
        "tipos_documento": "edital",
        "modalidade": "8", # 8 = Dispensa de Licitação (Lei 14.133/2021)
        "ordenacao": "-data",
        "pagina": 1,
        "tam_pagina": 30,
        "status": "aberto"
    }
    
    headers = {
        "User-Agent": "JRBC-Tech-Licitacoes/1.0 (contato@jrbctech.com.br)",
        "Accept": "application/json"
    }
    res = requests.get(url, params=params, headers=headers, timeout=30)
    if res.status_code != 200:
        return []

    editais = res.json().get("items", [])
    agora = datetime.now(timezone.utc)
    
    validos = []
    for ed in editais:
        # Validação estrita de prazo de recebimento de propostas
        data_fim_str = ed.get("data_fim_proposta") or ed.get("data_encerramento_proposta")
        if data_fim_str:
            try:
                data_fim = datetime.fromisoformat(data_fim_str.replace("Z", "+00:00"))
                if data_fim > agora:
                    validos.append(ed)
            except Exception:
                validos.append(ed)
        else:
            validos.append(ed)
            
    return validos

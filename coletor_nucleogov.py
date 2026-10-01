"""
Módulo: coletor_nucleogov.py
Finalidade: Monitoramento de Dispensas e Licitações em Câmaras e Prefeituras
do Estado do Tocantins que utilizam a plataforma NúcleoGov.
"""

import os
import logging
from datetime import datetime, timezone
import requests

HEADERS_NUCLEOGOV = {
    "X-NucleoGov-Services": "true",
    "User-Agent": "JRBC-Tech-Bot/2.0 (suporte@jrbctech.com.br)",
    "Accept": "application/json"
}

# Cidades no raio de atuação da JRBC TECH (Distância partindo de Palmas/TO)
MUNICIPIOS_NUCLEOGOV_TO = [
    {"nome": "Câmara de Paraíso do Tocantins", "url": "https://acessoainformacao.paraisodotocantins.to.leg.br", "distancia_km": 63},
    {"nome": "Câmara de Porto Nacional", "url": "https://acessoainformacao.portonacional.to.leg.br", "distancia_km": 60},
    {"nome": "Câmara de Miracema do Tocantins", "url": "https://acessoainformacao.miracematocantins.to.leg.br", "distancia_km": 75},
    {"nome": "Câmara de Guaraí", "url": "https://acessoainformacao.guarai.to.leg.br", "distancia_km": 180},
    {"nome": "Câmara de Colinas do Tocantins", "url": "https://acessoainformacao.colinas.to.leg.br", "distancia_km": 270},
    {"nome": "Câmara de Dianópolis", "url": "https://acessoainformacao.dianopolis.to.leg.br", "distancia_km": 340}
]

TERMOS_TI = [
    "informatica", "computador", "notebook", "impressora", "suporte",
    "manutencao", "cabeamento", "rede", "toner", "cartucho", "locacao",
    "help desk", "servidor", "estacao de trabalho", "telefonia"
]

def varrer_municipios_nucleogov() -> list[dict]:
    """Varre todas as cidades cadastradas buscando contratações de TI."""
    oportunidades = []

    for mun in MUNICIPIOS_NUCLEOGOV_TO:
        url_api = f"{mun['url']}/api/licitacoes"
        params = {"acao": "listar", "ano": datetime.now().year}

        try:
            res = requests.get(url_api, headers=HEADERS_NUCLEOGOV, params=params, timeout=12)
            if res.status_code == 200:
                itens = res.json().get("dados", []) or res.json() or []
                for item in itens:
                    texto = str(item).lower()
                    if any(termo in texto for termo in TERMOS_TI):
                        oportunidades.append({
                            "origem": "NUCLEOGOV",
                            "esfera": "MUNICIPAL",
                            "orgao_nome": mun["nome"],
                            "municipio": mun["nome"].replace("Câmara de ", ""),
                            "uf": "TO",
                            "distancia_palmas": mun["distancia_km"],
                            "numero_processo": item.get("numero") or item.get("processo") or "S/N",
                            "modalidade": item.get("modalidade") or "Dispensa Eletrônica",
                            "objeto": item.get("objeto") or item.get("descricao") or "Contratação de TI",
                            "valor_estimado": float(item.get("valor_estimado") or item.get("valor") or 0.0),
                            "link_edital": item.get("link") or f"{mun['url']}/cidadao/transparencia/licitacoes",
                            "id_unico": f"NUCLEO-{mun['nome'][:10]}-{item.get('numero') or hash(texto)}"
                        })
        except Exception:
            continue

    return oportunidades

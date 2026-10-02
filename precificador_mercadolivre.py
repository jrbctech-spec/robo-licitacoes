"""
Módulo: precificador_mercadolivre.py
Finalidade: Coleta de preços de mercado em tempo real usando a API Oficial do Mercado Livre (MLB)
para balizamento de custos e cálculo de margem em licitações e dispensas.
JRBC TECH - Palmas/TO
"""

import os
import re
import urllib.parse
import logging
from decimal import Decimal, ROUND_HALF_UP
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("PrecificadorML")

MELI_CLIENT_ID = os.getenv("MELI_CLIENT_ID")
MELI_CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET")
_meli_access_token = None

def _obter_token_meli():
    global _meli_access_token
    if not MELI_CLIENT_ID or not MELI_CLIENT_SECRET:
        logger.warning("Credenciais do Mercado Livre não configuradas no .env. A busca pode ser limitada.")
        return None
        
    try:
        res = requests.post(
            "https://api.mercadopago.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": MELI_CLIENT_ID,
                "client_secret": MELI_CLIENT_SECRET
            },
            timeout=10
        )
        if res.status_code == 200:
            _meli_access_token = res.json().get("access_token")
            return _meli_access_token
        else:
            logger.error(f"Erro ao obter token do Mercado Livre: {res.text}")
    except Exception as e:
        logger.error(f"Exceção ao gerar token ML: {e}")
    return None

def limpar_termo_busca(descricao_edital: str) -> str:
    """Extrai apenas as palavras essenciais do produto."""
    stopwords = [
        "aquisicao", "fornecimento", "de", "para", "com", "em", "um", "uma",
        "tipo", "unidade", "conforme", "termo", "referencia", "especificacoes"
    ]
    palavras = re.findall(r'\b[A-Za-z0-9/.-]+\b', descricao_edital.lower())
    filtradas = [p for p in palavras if p not in stopwords and len(p) > 1]
    return " ".join(filtradas[:5])

def consultar_precos_mercadolivre(termo_produto: str, limite: int = 5) -> dict:
    """Busca o produto na API do Mercado Livre."""
    termo_limpo = limpar_termo_busca(termo_produto)
    logger.info(f"Cotando via API ML: '{termo_limpo}'...")
    
    termo_encoded = urllib.parse.quote(termo_limpo)
    url_busca = f"https://api.mercadolibre.com/sites/MLB/search?q={termo_encoded}&condition=new&limit={limite}"

    headers = {}
    if _meli_access_token:
        headers["Authorization"] = f"Bearer {_meli_access_token}"
    elif MELI_CLIENT_ID and MELI_CLIENT_SECRET:
        # Tenta obter se não houver
        token = _obter_token_meli()
        if token:
            headers["Authorization"] = f"Bearer {token}"

    try:
        res = requests.get(url_busca, headers=headers, timeout=15)
        if res.status_code == 401 and MELI_CLIENT_ID:
            token = _obter_token_meli()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                res = requests.get(url_busca, headers=headers, timeout=15)

        if res.status_code != 200:
            return {"sucesso": False, "motivo": f"Status HTTP {res.status_code}"}

        dados = res.json()
        resultados = dados.get("results", [])
        
        if not resultados:
            return {"sucesso": False, "motivo": "Nenhum produto novo encontrado."}

        produtos_encontrados = []
        for item in resultados:
            preco = item.get("price")
            if preco is None:
                continue
            produtos_encontrados.append({
                "titulo": item.get("title", "Produto ML"),
                "preco": float(preco),
                "link": item.get("permalink", "")
            })

        if not produtos_encontrados:
            return {"sucesso": False, "motivo": "Sem preços válidos retornados."}

        produtos_encontrados.sort(key=lambda x: x["preco"])
        menor = produtos_encontrados[0]
        precos = [p["preco"] for p in produtos_encontrados]
        preco_medio = sum(precos) / len(precos)

        return {
            "sucesso": True,
            "termo_buscado": termo_limpo,
            "menor_preco": round(menor["preco"], 2),
            "preco_medio": round(preco_medio, 2),
            "melhor_titulo": menor["titulo"],
            "melhor_link": menor["link"],
            "total_avaliados": len(produtos_encontrados)
        }

    except Exception as e:
        logger.error(f"Erro na consulta ML API: {e}")
        return {"sucesso": False, "motivo": str(e)}

def analisar_viabilidade_ml(descricao_item: str, valor_referencia_governo: float) -> dict:
    cotacao = consultar_precos_mercadolivre(descricao_item)
    if not cotacao.get("sucesso"):
        return cotacao

    custo_base = Decimal(str(cotacao["menor_preco"]))
    frete_estimado = Decimal("35.00") if custo_base < Decimal("79.00") else Decimal("0.00")
    custo_total = custo_base + frete_estimado

    piso_venda = (custo_total * Decimal("1.30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ref_governo = Decimal(str(valor_referencia_governo or "0.00"))

    lucro_estimado = ref_governo - custo_total if ref_governo > 0 else Decimal("0.00")
    margem_estimada = ((lucro_estimado / custo_total) * 100).quantize(Decimal("0.1")) if custo_total > 0 else Decimal("0.0")

    eh_viavel = ref_governo >= piso_venda if ref_governo > 0 else False

    return {
        "sucesso": True,
        "termo": cotacao["termo_buscado"],
        "custo_ml": float(custo_base),
        "frete_estimado": float(frete_estimado),
        "custo_total": float(custo_total),
        "piso_minimo_30pct": float(piso_venda),
        "valor_referencia": float(ref_governo),
        "lucro_estimado": float(lucro_estimado),
        "margem_estimada_pct": float(margem_estimada),
        "viavel": eh_viavel,
        "titulo_oferta": cotacao["melhor_titulo"],
        "link_oferta": cotacao["melhor_link"]
    }

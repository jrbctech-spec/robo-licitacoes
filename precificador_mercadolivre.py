"""
Módulo: precificador_mercadolivre.py
Finalidade: Coleta de preços de mercado em tempo real no Mercado Livre Brasil (MLB) via Scraping
para balizamento de custos e cálculo de margem em licitações e dispensas.
JRBC TECH - Palmas/TO
"""

import os
import re
import urllib.parse
import logging
from decimal import Decimal, ROUND_HALF_UP
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("PrecificadorML")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive"
}

def limpar_termo_busca(descricao_edital: str) -> str:
    """Extrai apenas as palavras essenciais do produto (ex: marca, modelo, capacidade)."""
    stopwords = [
        "aquisicao", "fornecimento", "de", "para", "com", "em", "um", "uma",
        "tipo", "unidade", "conforme", "termo", "referencia", "especificacoes"
    ]
    palavras = re.findall(r'\b[A-Za-z0-9/.-]+\b', descricao_edital.lower())
    filtradas = [p for p in palavras if p not in stopwords and len(p) > 1]
    return " ".join(filtradas[:5])

def consultar_precos_mercadolivre(termo_produto: str, limite: int = 5) -> dict:
    """
    Busca o produto no Mercado Livre e retorna:
    - Menor Preço encontrado
    - Preço Médio
    - Título e Link da melhor oferta
    """
    termo_limpo = limpar_termo_busca(termo_produto)
    logger.info(f"Cotando no Mercado Livre (Scraping): '{termo_limpo}'...")
    
    termo_encoded = urllib.parse.quote(termo_limpo)
    url_busca = f"https://lista.mercadolivre.com.br/{termo_encoded}_Condicao_Novo"

    try:
        res = requests.get(url_busca, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            return {"sucesso": False, "motivo": f"Status HTTP {res.status_code}"}

        soup = BeautifulSoup(res.text, "html.parser")
        
        cards = soup.select(".ui-search-result, .ui-search-layout__item")
        if not cards:
            cards = soup.select("li.ui-search-layout__item")

        produtos_encontrados = []

        for card in cards[:limite]:
            try:
                titulo_elem = card.select_one(".poly-component__title, .ui-search-item__title")
                titulo = titulo_elem.text.strip() if titulo_elem else "Produto Mercado Livre"

                link_elem = card.select_one("a.poly-component__title, a.ui-search-link, a.ui-search-item__group__element")
                link = link_elem["href"] if link_elem and "href" in link_elem.attrs else url_busca

                fracao = card.select_one(".andes-money-amount__fraction")
                centavos = card.select_one(".andes-money-amount__cents")
                
                if fracao:
                    valor_str = fracao.text.replace(".", "").strip()
                    cents_str = centavos.text.strip() if centavos else "00"
                    preco = float(f"{valor_str}.{cents_str}")
                    
                    produtos_encontrados.append({
                        "titulo": titulo,
                        "preco": preco,
                        "link": link
                    })
            except Exception:
                continue

        if not produtos_encontrados:
            return {"sucesso": False, "motivo": "Nenhum produto novo encontrado para o termo."}

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
        logger.error(f"Erro na consulta Mercado Livre: {e}")
        return {"sucesso": False, "motivo": str(e)}

def analisar_viabilidade_ml(descricao_item: str, valor_referencia_governo: float) -> dict:
    """
    Compara o valor do edital com o custo real no Mercado Livre aplicando a margem de 30%.
    """
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

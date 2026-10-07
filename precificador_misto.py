import os
import requests
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("PrecificadorMisto")

class PrecificadorMisto:
    def __init__(self):
        self.meli_client_id = os.getenv("MELI_CLIENT_ID")
        self.meli_client_secret = os.getenv("MELI_CLIENT_SECRET")
        self.base_url = "https://api.mercadolibre.com"
        self._access_token: Optional[str] = None

    def _obter_token(self) -> str:
        if self._access_token:
            return self._access_token
            
        if not self.meli_client_id or not self.meli_client_secret:
            logger.warning("Credenciais do Mercado Livre não configuradas no .env")
            return ""
            
        try:
            url = f"{self.base_url}/oauth/token"
            data = {
                "grant_type": "client_credentials",
                "client_id": self.meli_client_id,
                "client_secret": self.meli_client_secret
            }
            resp = requests.post(url, data=data, timeout=5)
            if resp.status_code == 200:
                self._access_token = resp.json().get("access_token", "")
                return self._access_token
        except Exception as e:
            logger.error(f"Erro ao obter token do Mercado Livre: {e}")
            
        return ""

    def buscar_produto_meli(self, termo: str) -> Optional[Dict[str, Any]]:
        """Busca o produto mais barato no Mercado Livre que possua entrega Full."""
        token = self._obter_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        
        try:
            # q=termo, sort=price_asc, power_seller=yes, shipping_profile=full
            url = f"{self.base_url}/sites/MLB/search"
            params = {
                "q": termo,
                "sort": "price_asc",
                "limit": 10
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                resultados = resp.json().get("results", [])
                
                # Filtrar apenas Full (se possível com os dados retornados)
                for item in resultados:
                    shipping = item.get("shipping", {})
                    # Priorizar envios Full
                    if shipping.get("logistic_type") == "fulfillment":
                        return {
                            "titulo": item.get("title"),
                            "preco": item.get("price"),
                            "link": item.get("permalink"),
                            "id": item.get("id"),
                            "entrega_full": True
                        }
                
                # Fallback para o primeiro item se não tiver Full explícito
                if resultados:
                    item = resultados[0]
                    return {
                        "titulo": item.get("title"),
                        "preco": item.get("price"),
                        "link": item.get("permalink"),
                        "id": item.get("id"),
                        "entrega_full": False
                    }
        except Exception as e:
            logger.error(f"Erro ao buscar no Mercado Livre: {e}")
            
        return None

    def calcular_proposta(self, preco_produto: float, qtd_produto: int, margem_produto: float, valor_servico: float) -> Dict[str, float]:
        """
        margem_produto: em formato percentual decimal (ex: 0.25 para 25%)
        """
        custo_total_produtos = preco_produto * qtd_produto
        venda_total_produtos = custo_total_produtos * (1 + margem_produto)
        
        # Mão de obra tem 100% de margem, ou seja, o valor que cobramos é lucro líquido?
        # A regra de negócio diz: "com 100% de margem líquida para a JRBC TECH", 
        # interpretaremos que o valor do serviço cobrado vai inteiro como margem, ou que é o preço de venda e o custo é 0 para simplificar?
        # Vamos assumir que "valor_servico" é o preço cobrado do órgão, e o custo não entra na conta (ou custo = 0), ou valor_servico é o lucro desejado.
        # "aplique o valor da mão de obra ... com 100% margem" = o valor cobrado é o lucro.
        valor_proposta = venda_total_produtos + valor_servico
        lucro_liquido = (venda_total_produtos - custo_total_produtos) + valor_servico
        margem_combinada = (lucro_liquido / valor_proposta * 100) if valor_proposta > 0 else 0.0
        
        return {
            "custo_produtos": custo_total_produtos,
            "custo_real": custo_total_produtos,
            "valor_proposta": valor_proposta,
            "lucro_liquido": lucro_liquido,
            "margem_lucro": margem_combinada,
            "stop_loss": custo_total_produtos
        }

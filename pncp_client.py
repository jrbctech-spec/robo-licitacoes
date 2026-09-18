"""
pncp_client.py – Módulo para consultar contratações públicas abertas na API pública do PNCP.

Documentação PNCP: https://pncp.gov.br/pncp-api/v1/
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import requests
from requests.exceptions import RequestException

from config import config

logger = logging.getLogger(__name__)


class PNCPError(Exception):
    """Exceção base para erros na API do PNCP."""
    pass


class RateLimitError(PNCPError):
    """Exceção lançada quando atinge o rate limit (HTTP 429)."""
    pass


class PNCPClient:
    """
    Cliente HTTP resiliente para consumo da API pública do Portal Nacional de Contratações Públicas (PNCP).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or config.pncp_base_url).rstrip("/") + "/"
        self.timeout = timeout or config.http_timeout
        self.max_retries = max_retries or config.max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "RoboLicitacoes-JRBC/1.0",
            "Accept": "application/json",
        })

    def _fazer_requisicao(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executa uma requisição GET na API do PNCP com tratamento de rate limit e retentativa exponencial.
        """
        delay = 1.0

        for tentativa in range(1, self.max_retries + 1):
            try:
                logger.debug(f"PNCP GET {url} (Tentativa {tentativa}/{self.max_retries}) | Params: {params}")
                response = self.session.get(url, params=params, timeout=self.timeout)

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    logger.warning(f"Rate limit do PNCP (429) atingido. Aguardando {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    delay *= 2.0
                    continue

                if response.status_code in (404, 400):
                    logger.warning(f"Resposta HTTP {response.status_code} da API PNCP: {url}")
                    return {"data": [], "totalRegistros": 0}

                response.raise_for_status()

                data = response.json()
                if isinstance(data, dict):
                    return data
                elif isinstance(data, list):
                    return {"data": data, "totalRegistros": len(data)}
                return {"data": [], "totalRegistros": 0}

            except requests.HTTPError as http_err:
                status = response.status_code if 'response' in locals() and response is not None else 0
                if status == 429 and tentativa < self.max_retries:
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                logger.error(f"Erro HTTP {status} na API PNCP ({url}): {http_err}")
                if tentativa == self.max_retries:
                    raise PNCPError(f"Erro HTTP {status} na API PNCP: {http_err}") from http_err

            except RequestException as req_err:
                logger.warning(f"Erro de conexão na API PNCP ({url}) tentativa {tentativa}: {req_err}")
                if tentativa == self.max_retries:
                    raise PNCPError(f"Falha de conexão com a API do PNCP: {req_err}") from req_err

            time.sleep(delay)
            delay *= 2.0

        raise PNCPError("Número máximo de tentativas atingido sem sucesso.")

    def buscar_contratacoes_publicacao(
        self,
        uf: str,
        data_inicial: Optional[str] = None,
        data_final: Optional[str] = None,
        codigo_modalidade: Optional[int] = None,
        pagina: int = 1,
        tamanho_pagina: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Consulta publicações de contratações abertas por UF e intervalo de datas na API do PNCP.
        """
        hoje = datetime.now()
        if not data_final:
            data_final = hoje.strftime("%Y%m%d")
        if not data_inicial:
            recuo = timedelta(days=config.dias_publicacao_recuo)
            data_inicial = (hoje - recuo).strftime("%Y%m%d")

        data_inicial = data_inicial.replace("-", "")
        data_final = data_final.replace("-", "")

        params: Dict[str, Any] = {
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "uf": uf.upper(),
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }

        if codigo_modalidade is not None:
            params["codigoModalidadeContratacao"] = codigo_modalidade

        urls_para_tentar = [
            f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao",
            f"{self.base_url}contratacoes/publicacao",
            f"https://pncp.gov.br/api/consulta/v1/contratacoes/proposta",
            f"{self.base_url}contratacoes/proposta",
        ]

        for url in urls_para_tentar:
            try:
                res = self._fazer_requisicao(url, params=params)
                data = res.get("data") or res.get("result") or []
                if isinstance(data, list) and len(data) > 0:
                    return data
            except PNCPError as err:
                logger.warning(f"Tentativa na URL {url} falhou: {err}")

        return []

    def buscar_todas_contratacoes_uf(
        self,
        uf: str,
        dias_recuo: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca contratações para a UF especificada.
        """
        hoje = datetime.now()
        recuo = dias_recuo or config.dias_publicacao_recuo
        dt_inicio = (hoje - timedelta(days=recuo)).strftime("%Y%m%d")
        dt_fim = hoje.strftime("%Y%m%d")

        try:
            return self.buscar_contratacoes_publicacao(
                uf=uf,
                data_inicial=dt_inicio,
                data_final=dt_fim,
            )
        except Exception as err:
            logger.error(f"Erro ao buscar contratações para UF {uf}: {err}")
            return []

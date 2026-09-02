"""
pncp_client.py – Cliente oficial para a API pública do Portal Nacional de Contratações Públicas (PNCP).

Documentação OpenAPI: https://pncp.gov.br/api/consulta/swagger-ui/index.html

Métodos principais:
  - buscar_compra(cnpj_orgao, ano, sequencial): Retorna metadados do certame, modalidade e modo de disputa.
  - obter_itens_compra(cnpj_orgao, ano, sequencial): Extrai todos os itens, quantidades, valores estimados e descrições.
  - obter_arquivos_edital(cnpj_orgao, ano, sequencial): Baixa os PDFs oficiais anexados ao processo no PNCP.
  - sincronizar_pncp_supabase(cnpj_orgao, ano, sequencial): Salva e sincroniza os registros nas tabelas 'licitacoes' e 'itens_licitacao' do Supabase.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from supabase_client import inserir_item, inserir_licitacao

logger = logging.getLogger(__name__)

BASE_URL_PNCP = "https://pncp.gov.br/api/consulta/v1"
HEADERS_HTTP = {
    "User-Agent": "RoboLicitacoes/1.0 (Automation Tool)",
    "Accept": "application/json",
}


def extrair_params_pncp(url_ou_texto: str) -> tuple[str, str, str]:
    """
    Extrai (cnpj_orgao, ano, sequencial) de uma URL do PNCP ou string formatada.

    Exemplos:
      - https://pncp.gov.br/app/editais/25053190000136/2026/000274
      - 25053190000136/2026/000274
      - CNPJ: 25053190000136 Ano: 2026 Seq: 274
    """
    texto = url_ou_texto.strip()
    match = re.search(r"(\d{14})[/\s_-]*(\d{4})[/\s_-]*(\d+)", texto)
    if not match:
        raise ValueError(
            "Parâmetros PNCP inválidos. Formato aceito: CNPJ/ANO/SEQUENCIAL "
            "(ex: 25053190000136/2026/000274 ou URL do PNCP)"
        )
    
    cnpj, ano, sequencial = match.groups()
    return cnpj, ano, str(int(sequencial))


def buscar_compra(cnpj_orgao: str, ano: str, sequencial: str) -> dict[str, Any]:
    """
    Retorna os metadados do certame, modalidade e modo de disputa do PNCP.
    Endpoint: GET /orgaos/{cnpj}/compras/{ano}/{sequencial}
    """
    url = f"{BASE_URL_PNCP}/orgaos/{cnpj_orgao}/compras/{ano}/{sequencial}"
    logger.info(f"PNCP buscar_compra: {url}")
    
    with httpx.Client(timeout=15.0, headers=HEADERS_HTTP) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def obter_itens_compra(cnpj_orgao: str, ano: str, sequencial: str) -> list[dict[str, Any]]:
    """
    Extrai todos os itens, quantidades, valores estimados e descrições do PNCP.
    Endpoint: GET /orgaos/{cnpj}/compras/{ano}/{sequencial}/itens
    """
    url = f"{BASE_URL_PNCP}/orgaos/{cnpj_orgao}/compras/{ano}/{sequencial}/itens?pagina=1&tamanhoPagina=500"
    logger.info(f"PNCP obter_itens_compra: {url}")
    
    try:
        with httpx.Client(timeout=15.0, headers=HEADERS_HTTP) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return data.get("result", data.get("itens", []))
    except Exception as exc:
        logger.warning(f"Erro ao buscar itens da compra no PNCP: {exc}")
    
    return []


def obter_arquivos_edital(
    cnpj_orgao: str,
    ano: str,
    sequencial: str,
    pasta_destino: str | Path = "downloads",
) -> list[str]:
    """
    Baixa os PDFs oficiais anexados ao processo no PNCP para a pasta destino.
    Endpoint: GET /orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos
    """
    url = f"{BASE_URL_PNCP}/orgaos/{cnpj_orgao}/compras/{ano}/{sequencial}/arquivos"
    logger.info(f"PNCP obter_arquivos_edital: {url}")
    
    with httpx.Client(timeout=15.0, headers=HEADERS_HTTP) as client:
        resp = client.get(url)
        resp.raise_for_status()
        arquivos = resp.json()

    if not arquivos:
        logger.warning("Nenhum arquivo anexado encontrado para este certame no PNCP.")
        return []

    pasta = Path(pasta_destino)
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos_baixados: list[str] = []

    with httpx.Client(timeout=30.0, headers=HEADERS_HTTP, follow_redirects=True) as client:
        for idx, arq in enumerate(arquivos, 1):
            url_dl = arq.get("url") or arq.get("uri")
            titulo = arq.get("titulo") or f"edital_pncp_{idx}.pdf"
            if not titulo.lower().endswith(".pdf"):
                titulo += ".pdf"
            
            if url_dl:
                nome_limpo = re.sub(r"[^\w\-.]", "_", titulo)
                caminho_local = pasta / f"pncp_{cnpj_orgao}_{ano}_{sequencial}_{nome_limpo}"
                try:
                    r = client.get(url_dl)
                    r.raise_for_status()
                    caminho_local.write_bytes(r.content)
                    caminhos_baixados.append(str(caminho_local))
                    logger.info(f"📥 PDF baixado do PNCP: {caminho_local.name}")
                except Exception as exc:
                    logger.warning(f"Falha ao baixar arquivo '{titulo}' do PNCP: {exc}")

    return caminhos_baixados


def sincronizar_pncp_supabase(cnpj_orgao: str, ano: str, sequencial: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Consome o PNCP e sincroniza os dados diretamente com as tabelas 'licitacoes' e 'itens_licitacao' no Supabase.

    Returns:
        Tupla (registro_licitacao_supabase, lista_itens_supabase)
    """
    compra = buscar_compra(cnpj_orgao, ano, sequencial)
    itens_pncp = obter_itens_compra(cnpj_orgao, ano, sequencial)

    orgao_nome = compra.get("orgaoEntidade", {}).get("razaoSocial", f"Órgão CNPJ {cnpj_orgao}")
    objeto = compra.get("objeto", "Licitação via PNCP")
    num_pregao = f"{sequencial}/{ano}"
    num_uasg = compra.get("unidadeOrgao", {}).get("codigoUnidade", cnpj_orgao[:6])
    modo_disputa = compra.get("modoDisputaNome", "aberto").lower()

    dados_licitacao = {
        "numero_uasg": str(num_uasg),
        "numero_pregao": num_pregao,
        "orgao": orgao_nome[:250],
        "objeto": objeto[:500],
        "modo_disputa": "aberto_fechado" if "fechado" in modo_disputa else "aberto",
        "url_sala_disputa": f"https://pncp.gov.br/app/editais/{cnpj_orgao}/{ano}/{sequencial}",
        "status": "configurada",
    }

    logger.info(f"Sincronizando licitação {num_pregao} (UASG {num_uasg}) com Supabase...")
    lic_salva = inserir_licitacao(dados_licitacao)
    lic_id = lic_salva.get("id") or str(uuid.uuid4())

    itens_salvos: list[dict[str, Any]] = []
    for i_pncp in itens_pncp:
        num_item = int(i_pncp.get("numeroItem", i_pncp.get("item", 1)))
        desc = i_pncp.get("descricao", f"Item {num_item}")
        qtd = float(i_pncp.get("quantidade", 1.0))
        unidade = str(i_pncp.get("unidadeMedida", "UN")).upper()
        val_unit = i_pncp.get("valorUnitarioEstimado", i_pncp.get("valorTotal", None))
        val_ref = float(val_unit) if val_unit else None

        dados_item = {
            "licitacao_id": lic_id,
            "numero_item": num_item,
            "descricao": str(desc)[:300],
            "unidade_medida": unidade,
            "quantidade": qtd,
            "valor_referencia": val_ref,
            "custo_unitario": 0.0,
            "margem_minima_percent": 15.0,
            "preco_minimo": 0.0,
            "preco_inicial_proposta": val_ref * 0.985 if val_ref else 0.0,
        }

        try:
            it_salvo = inserir_item(dados_item)
            itens_salvos.append(it_salvo)
        except Exception as exc:
            logger.warning(f"Erro ao sincronizar item {num_item} com Supabase: {exc}")

    logger.info(f"✅ PNCP sincronizado com Supabase: {len(itens_salvos)} item(ns) salvos.")
    return lic_salva, itens_salvos


def buscar_licitacoes_propostas_pncp(
    uf: str = "TO",
    data_final: str = "20261231",
    pagina: int = 1,
    tamanho_pagina: int = 20,
) -> dict[str, Any]:
    """
    Busca licitações com propostas abertas no PNCP por UF (Estado) e data limite.
    Endpoint: GET https://pncp.gov.br/api/consulta/v1/contratacoes/proposta
    """
    url = f"{BASE_URL_PNCP}/contratacoes/proposta"
    params = {
        "dataFinal": data_final,
        "uf": uf.upper(),
        "pagina": pagina,
        "tamanhoPagina": max(10, tamanho_pagina),
    }

    logger.info(f"PNCP buscar_licitacoes_propostas_pncp: UF={uf}, dataFinal={data_final}")
    with httpx.Client(timeout=30.0, headers=HEADERS_HTTP) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def buscar_publicacoes_pncp(
    data_inicial: Optional[str] = None,
    data_final: Optional[str] = None,
    codigo_modalidade: Optional[int] = None,
    uf: Optional[str] = None,
    palavra_chave: Optional[str] = None,
    pagina: int = 1,
    tamanho_pagina: int = 20,
) -> dict[str, Any]:
    """
    Consulta o endpoint oficial da API do PNCP para publicações de licitação.
    Endpoint: GET https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao

    Filtros suportados:
      - data_inicial: YYYYMMDD (padrão: 30 dias atrás)
      - data_final: YYYYMMDD (padrão: 30 dias à frente)
      - codigo_modalidade: ex 6 (Pregão Eletrônico), 8 (Dispensa), 4 (Concorrência)
      - uf: Sigla do estado (TO, SP, DF, etc.)
      - palavra_chave: Filtro de texto no objeto/órgão
      - pagina, tamanho_pagina: Paginação (mínimo 10 por página)
    """
    from datetime import datetime, timedelta

    hoje = datetime.now()
    if not data_inicial:
        data_inicial = (hoje - timedelta(days=30)).strftime("%Y%m%d")
    if not data_final:
        data_final = (hoje + timedelta(days=30)).strftime("%Y%m%d")

    url = f"{BASE_URL_PNCP}/contratacoes/publicacao"
    params: dict[str, Any] = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "pagina": pagina,
        "tamanhoPagina": max(10, tamanho_pagina),
    }

    if codigo_modalidade is not None and codigo_modalidade > 0:
        params["codigoModalidadeContratacao"] = codigo_modalidade

    if uf and uf.strip().upper() != "TODAS":
        params["uf"] = uf.strip().upper()

    logger.info(f"PNCP buscar_publicacoes_pncp: params={params}")
    with httpx.Client(timeout=30.0, headers=HEADERS_HTTP) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data_json = resp.json()

    # Aplica filtro por palavra-chave localmente
    if palavra_chave and palavra_chave.strip():
        kw = palavra_chave.strip().lower()
        itens_orig = data_json.get("data", [])
        itens_filtrados = [
            i for i in itens_orig
            if kw in i.get("objetoCompra", "").lower() or kw in i.get("orgaoEntidade", {}).get("razaoSocial", "").lower()
        ]
        data_json["data"] = itens_filtrados
        data_json["totalRegistros"] = len(itens_filtrados)

    return data_json


def baixar_anexos_edital_pncp(
    cnpj_orgao: str,
    ano: str,
    sequencial: str,
    pasta_destino: str | Path = "downloads_editais",
) -> list[str]:
    """
    Baixa automaticamente os PDFs anexados ao processo no PNCP para a pasta downloads_editais/.
    """
    return obter_arquivos_edital(cnpj_orgao, ano, sequencial, pasta_destino=pasta_destino)


# Alias retrocompatíveis
consultar_compra_pncp = buscar_compra
consultar_itens_pncp = obter_itens_compra
consultar_arquivos_pncp = lambda cnpj, ano, seq: [{"url": u} for u in obter_arquivos_edital(cnpj, ano, seq)]
baixar_edital_pncp = lambda cnpj, ano, seq, pasta="downloads_editais": (obter_arquivos_edital(cnpj, ano, seq, pasta) or [None])[0]
carregar_certame_pncp = lambda url: (buscar_compra(*extrair_params_pncp(url)), obter_itens_compra(*extrair_params_pncp(url)), (obter_arquivos_edital(*extrair_params_pncp(url)) or [None])[0])



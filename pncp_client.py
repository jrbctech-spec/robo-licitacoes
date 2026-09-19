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
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

import unicodedata
import httpx


def remover_acentos(texto: str) -> str:
    """Remove acentos e caracteres especiais para normalização de buscas de texto."""
    if not texto:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()

from supabase_client import inserir_item, inserir_licitacao


class ModalidadePNCPEnum(str, Enum):
    """
    Modalidades de licitação e contratação segundo a Lei 14.133/2021.
    """
    DISPENSA_ELETRONICA = "Dispensa Eletrônica (Lei 14.133/21, Art. 75)"
    PREGAO_ELETRONICO = "Pregão Eletrônico"
    CONTRATACAO_DIRETA = "Contratação Direta"
    CONCORRENCIA_ELETRONICA = "Concorrência Eletrônica"
    OUTROS = "Outras Modalidades"


class ItemCertamePNCP(BaseModel):
    """Item individual extraído da API do PNCP."""
    numero_item: int
    descricao: str
    quantidade: float = 1.0
    unidade_medida: str = "UN"
    valor_referencia: Optional[float] = None
    tipo_item: str = "PRODUTO"  # PRODUTO ou SERVICO


class CertameMinadoPNCP(BaseModel):
    """Dados completos e classificados de um certame minerado no PNCP."""
    cnpj_orgao: str
    ano: str
    sequencial: str
    numero_uasg: str
    numero_pregao: str
    orgao_nome: str
    objeto: str
    modalidade: ModalidadePNCPEnum = ModalidadePNCPEnum.PREGAO_ELETRONICO
    modo_disputa: str = "aberto"
    data_hora_sessao: Optional[str] = None
    url_certame: str = ""
    itens: list[ItemCertamePNCP] = Field(default_factory=list)
    arquivos_baixados: list[str] = Field(default_factory=list)
    pasta_certame: str = ""


def identificar_modalidade_pncp(dados_compra: dict[str, Any]) -> ModalidadePNCPEnum:
    """
    Identifica com precisão a modalidade exata conforme os metadados do PNCP e Lei 14.133/2021.
    """
    cod_modalidade = dados_compra.get("modalidadeId") or dados_compra.get("codigoModalidadeContratacao")
    nome_modalidade = str(dados_compra.get("modalidadeNome") or dados_compra.get("modalidadeContratacaoNome") or "").lower()

    if cod_modalidade == 8 or "dispensa" in nome_modalidade or "art. 75" in nome_modalidade or "artigo 75" in nome_modalidade:
        return ModalidadePNCPEnum.DISPENSA_ELETRONICA
    elif cod_modalidade == 6 or "pregã" in nome_modalidade or "pregao" in nome_modalidade:
        return ModalidadePNCPEnum.PREGAO_ELETRONICO
    elif cod_modalidade in (9, 10) or "inexigib" in nome_modalidade or "direta" in nome_modalidade:
        return ModalidadePNCPEnum.CONTRATACAO_DIRETA
    elif cod_modalidade == 4 or "concorr" in nome_modalidade:
        return ModalidadePNCPEnum.CONCORRENCIA_ELETRONICA

    return ModalidadePNCPEnum.OUTROS

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

    try:
        with httpx.Client(timeout=15.0, headers=HEADERS_HTTP, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                logger.warning(f"Compra não encontrada (404): {url}")
                raise ValueError(f"Compra não encontrada para {cnpj_orgao}/{ano}/{sequencial}")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 404:
            raise ValueError(f"Compra não encontrada para {cnpj_orgao}/{ano}/{sequencial}")
        logger.error(f"Erro HTTP ao buscar compra no PNCP: {exc}")
        raise
    except Exception as exc:
        logger.error(f"Erro inesperado ao buscar compra no PNCP: {exc}")
        raise


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

    NOTA: Este endpoint pode retornar 404 se a licitação estiver encerrada ou
    se o endpoint não estiver disponível na versão da API atual. Nesse caso,
    retorna lista vazia sem erro fatal.
    """
    url = f"{BASE_URL_PNCP}/orgaos/{cnpj_orgao}/compras/{ano}/{sequencial}/arquivos"
    logger.info(f"PNCP obter_arquivos_edital: {url}")

    try:
        with httpx.Client(timeout=15.0, headers=HEADERS_HTTP, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                # Endpoint não existe ou licitação encerrada sem arquivos
                logger.warning(
                    f"Endpoint de arquivos não disponível (404) para "
                    f"{cnpj_orgao}/{ano}/{sequencial}. "
                    f"Verifique se a licitação ainda está ativa ou se o endpoint "
                    f"está disponível na versão atual da API PNCP."
                )
                return []
            resp.raise_for_status()
            arquivos = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 404:
            logger.warning(f"Endpoint de arquivos não disponível (404): {exc.request.url}")
            return []
        logger.error(f"Erro HTTP ao buscar arquivos do PNCP: {exc}")
        return []
    except Exception as exc:
        logger.error(f"Erro inesperado ao buscar arquivos do PNCP: {exc}")
        return []

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
      - data_inicial: YYYYMMDD (padrão: 20 dias atrás)
      - data_final: YYYYMMDD (padrão: hoje)
      - codigo_modalidade: ex 6 (Pregão Eletrônico), 8 (Dispensa), 4 (Concorrência).
                          Se None ou 'TODAS', consulta as modalidades principais (6, 8, 4).
      - uf: Sigla do estado (TO, SP, DF, etc.) ou None para todo o Brasil
      - palavra_chave: Filtro de texto no objeto/órgão
      - pagina, tamanho_pagina: Paginação
    """
    from datetime import datetime, timedelta

    hoje = datetime.now()
    if not data_final:
        data_final = hoje.strftime("%Y%m%d")
    if not data_inicial:
        data_inicial = (hoje - timedelta(days=20)).strftime("%Y%m%d")

    modalidades_para_consultar = (
        [codigo_modalidade] if (codigo_modalidade is not None and codigo_modalidade > 0) else [6, 8, 4]
    )

    url = f"{BASE_URL_PNCP}/contratacoes/publicacao"
    todos_itens: list[dict[str, Any]] = []
    total_encontrados = 0

    with httpx.Client(timeout=12.0, headers=HEADERS_HTTP) as client:
        for mod in modalidades_para_consultar:
            params: dict[str, Any] = {
                "dataInicial": data_inicial,
                "dataFinal": data_final,
                "codigoModalidadeContratacao": mod,
                "pagina": pagina,
                "tamanhoPagina": max(10, min(50, tamanho_pagina)),
            }
            if uf and uf.strip().upper() != "TODAS":
                params["uf"] = uf.strip().upper()

            try:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data_json = resp.json()
                    itens = data_json.get("data", [])
                    todos_itens.extend(itens)
                    total_encontrados += data_json.get("totalRegistros", len(itens))
                else:
                    logger.warning(f"Aviso PNCP modalidade {mod}: HTTP {resp.status_code} - {resp.text[:120]}")
            except Exception as exc_req:
                logger.warning(f"Erro ao consultar modalidade {mod} no PNCP: {exc_req}")

    # Aplica filtro por palavra-chave localmente com suporte a acentos
    if palavra_chave and palavra_chave.strip():
        kw_norm = remover_acentos(palavra_chave.strip())
        todos_itens = [
            i for i in todos_itens
            if kw_norm in remover_acentos(i.get("objetoCompra") or "")
            or kw_norm in remover_acentos(i.get("orgaoEntidade", {}).get("razaoSocial") or "")
        ]

    return {
        "data": todos_itens,
        "totalRegistros": len(todos_itens),
        "numeroPagina": pagina,
        "empty": len(todos_itens) == 0,
    }


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


def minar_e_estruturar_certame(
    url_ou_texto: str,
    base_dir: str | Path = "processos_pncp",
) -> CertameMinadoPNCP:
    """
    Mineração e classificação completa de um certame no PNCP:
    1. Identifica parâmetros (CNPJ/Ano/Sequencial)
    2. Identifica modalidade exata (Dispensa Eletrônica, Pregão, etc)
    3. Cria a estrutura de pastas organizada (UASG_MODALIDADE_NUMERO)
    4. Realiza o download automático de todos os arquivos oficiais (Edital, TR, Apêndices, Propostas)
    5. Extrai itens, quantidades, valores de referência e data/hora da sessão pública.
    """
    cnpj_orgao, ano, sequencial = extrair_params_pncp(url_ou_texto)
    compra = buscar_compra(cnpj_orgao, ano, sequencial)
    itens_raw = obter_itens_compra(cnpj_orgao, ano, sequencial)

    modalidade = identificar_modalidade_pncp(compra)
    orgao_nome = compra.get("orgaoEntidade", {}).get("razaoSocial", f"Órgão CNPJ {cnpj_orgao}")
    objeto = compra.get("objeto", "Licitação via PNCP")
    num_uasg = str(compra.get("unidadeOrgao", {}).get("codigoUnidade", cnpj_orgao[:6]))
    num_pregao = f"{sequencial}_{ano}"

    # Formatar nome da pasta de forma padronizada: UASG_MODALIDADE_NUMERO
    mod_tag = "DISPENSA" if modalidade == ModalidadePNCPEnum.DISPENSA_ELETRONICA else "PREGAO"
    nome_pasta = f"UASG_{num_uasg}_{mod_tag}_{num_pregao}"
    pasta_destino = Path(base_dir) / nome_pasta
    pasta_destino.mkdir(parents=True, exist_ok=True)

    # Download de anexos oficiais
    arquivos_baixados = obter_arquivos_edital(cnpj_orgao, ano, sequencial, pasta_destino=pasta_destino)

    # Extração de itens
    itens_certame: list[ItemCertamePNCP] = []
    for raw in itens_raw:
        num_i = int(raw.get("numeroItem", raw.get("item", 1)))
        desc_i = str(raw.get("descricao", f"Item {num_i}"))
        qtd_i = float(raw.get("quantidade", 1.0))
        un_i = str(raw.get("unidadeMedida", "UN")).upper()
        v_ref = raw.get("valorUnitarioEstimado") or raw.get("valorTotal")
        val_ref = float(v_ref) if v_ref else None

        itens_certame.append(ItemCertamePNCP(
            numero_item=num_i,
            descricao=desc_i,
            quantidade=qtd_i,
            unidade_medida=un_i,
            valor_referencia=val_ref,
        ))

    data_sessao = compra.get("dataAberturaProposta") or compra.get("dataEncerramentoProposta")

    return CertameMinadoPNCP(
        cnpj_orgao=cnpj_orgao,
        ano=ano,
        sequencial=sequencial,
        numero_uasg=num_uasg,
        numero_pregao=f"{sequencial}/{ano}",
        orgao_nome=orgao_nome,
        objeto=objeto,
        modalidade=modalidade,
        modo_disputa=compra.get("modoDisputaNome", "aberto"),
        data_hora_sessao=data_sessao,
        url_certame=f"https://pncp.gov.br/app/editais/{cnpj_orgao}/{ano}/{sequencial}",
        itens=itens_certame,
        arquivos_baixados=arquivos_baixados,
        pasta_certame=str(pasta_destino),
    )


# Alias retrocompatíveis
consultar_compra_pncp = buscar_compra
consultar_itens_pncp = obter_itens_compra
consultar_arquivos_pncp = lambda cnpj, ano, seq: [{"url": u} for u in obter_arquivos_edital(cnpj, ano, seq)]
baixar_edital_pncp = lambda cnpj, ano, seq, pasta="downloads_editais": (obter_arquivos_edital(cnpj, ano, seq, pasta) or [None])[0]
carregar_certame_pncp = lambda url: (buscar_compra(*extrair_params_pncp(url)), obter_itens_compra(*extrair_params_pncp(url)), (obter_arquivos_edital(*extrair_params_pncp(url)) or [None])[0])



"""
drive_scanner.py – Leitor e scanner automático 100% local de pastas de processos de licitação.

Funcionalidades:
  1. Varre pastas locais de processos onde cada subpasta representa um Órgão/UASG ou Processo.
  2. Identifica editais em PDF (.pdf).
  3. Envia os PDFs para o edital_parser.py para extração de regras e itens.
  4. Sincroniza os editais e itens encontrados nas tabelas 'licitacoes' e 'itens_licitacao' do Supabase.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from edital_parser import EditalParser, ResultadoEdital
from supabase_client import inserir_item, inserir_licitacao

logger = logging.getLogger(__name__)


def sincronizar_resultado_supabase(resultado: ResultadoEdital) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Sincroniza um ResultadoEdital lido com as tabelas 'licitacoes' e 'itens_licitacao' do Supabase.
    """
    num_uasg = resultado.numero_uasg or "000000"
    num_pregao = resultado.numero_pregao or "0/0"
    orgao = resultado.orgao or Path(resultado.caminho_pdf).parent.name or "Órgão Desconhecido"
    objeto = resultado.objeto or "Aquisição/Contratação de Serviços"

    dados_lic = {
        "numero_uasg": str(num_uasg),
        "numero_pregao": num_pregao,
        "orgao": orgao[:250],
        "objeto": objeto[:500],
        "modo_disputa": resultado.regras.modo_disputa or "aberto",
        "url_sala_disputa": "",
        "status": "configurada",
    }

    logger.info(f"Gravando edital {num_pregao} (UASG {num_uasg}) no Supabase...")
    lic_salva = inserir_licitacao(dados_lic)
    lic_id = lic_salva.get("id") or str(uuid.uuid4())

    itens_salvos: list[dict[str, Any]] = []
    for item in resultado.itens:
        val_ref = item.valor_referencia
        dados_item = {
            "licitacao_id": lic_id,
            "numero_item": item.numero_item,
            "descricao": item.descricao[:300],
            "unidade_medida": item.unidade_medida or "UN",
            "quantidade": item.quantidade or 1.0,
            "valor_referencia": val_ref,
            "custo_unitario": 0.0,
            "margem_minima_percent": 15.0,
            "preco_minimo": 0.0,
            "preco_inicial_proposta": val_ref * 0.985 if val_ref else 0.0,
            "decremento_minimo": item.decremento_minimo,
            "decremento_percentual": item.decremento_percentual,
        }

        try:
            it_salvo = inserir_item(dados_item)
            itens_salvos.append(it_salvo)
        except Exception as exc:
            logger.warning(f"Erro ao salvar item {item.numero_item} no Supabase: {exc}")

    logger.info(f"✅ Sincronizados {len(itens_salvos)} item(ns) da licitação {num_pregao} no Supabase.")
    return lic_salva, itens_salvos


from drive_uploader import DriveUploader


def escanear_pasta_local(
    caminho_raiz: str | Path,
    sincronizar_supabase: bool = True,
    organizar_em_subpastas: bool = True,
) -> list[ResultadoEdital]:
    """
    Percorre uma pasta local contendo subpastas de processos/UASGs.
    Identifica editais PDF, parseia, organiza em estrutura UASG_MODALIDADE_NUMERO
    e sincroniza com o Supabase.
    """
    raiz = Path(caminho_raiz)
    if not raiz.exists() or not raiz.is_dir():
        logger.warning(f"Caminho local inválido para escanear: {raiz}")
        return []

    logger.info(f"🔍 Escaneando diretório local de processos: {raiz}")
    parser = EditalParser()
    uploader = DriveUploader(base_dir=raiz) if organizar_em_subpastas else None
    resultados: list[ResultadoEdital] = []

    arquivos_pdf = list(raiz.glob("**/*.pdf")) + list(raiz.glob("**/*.PDF"))
    logger.info(f"Encontrados {len(arquivos_pdf)} arquivo(s) PDF para analisar.")

    for pdf_path in arquivos_pdf:
        try:
            logger.info(f"Analisando PDF local: {pdf_path.name}")
            res = parser.parsear(pdf_path)
            if res.itens or res.sucesso:
                resultados.append(res)
                if uploader and res.numero_uasg and res.numero_pregao:
                    try:
                        uploader.salvar_arquivo_certame(
                            numero_uasg=res.numero_uasg,
                            modalidade=res.regras.modo_disputa or "PREGAO",
                            numero_pregao=res.numero_pregao,
                            caminho_arquivo=pdf_path,
                            subcategoria="editais_anexos",
                        )
                    except Exception as exc_org:
                        logger.warning(f"Aviso ao organizar arquivo local: {exc_org}")

                if sincronizar_supabase:
                    sincronizar_resultado_supabase(res)
        except Exception as exc:
            logger.error(f"Erro ao processar PDF '{pdf_path.name}': {exc}")

    return resultados

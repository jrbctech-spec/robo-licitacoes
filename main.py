"""
main.py – Script principal do Robô de Licitações da JRBC TECH.

Busca contratações públicas abertas na API do PNCP para os estados configurados (padrão TO, GO),
aplica o filtro de palavras-chave de TI no objeto da licitação e imprime a lista formatada no terminal.
"""

import sys
import logging
import unicodedata
from typing import Any, Dict, List, Optional
import requests

from config import config
from pncp_client import PNCPClient, PNCPError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def remover_acentos(texto: str) -> str:
    """Remove acentos e diacríticos de uma string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def extrair_texto_objeto(item: Dict[str, Any]) -> str:
    """Extrai a descrição do objeto de um registro retornado pela API do PNCP."""
    objeto = (
        item.get("objetoCompra")
        or item.get("objeto")
        or item.get("descricaoObjeto")
        or item.get("descricao")
        or ""
    )
    return str(objeto).strip()


def extrair_orgao_uasg_uf(item: Dict[str, Any], uf_fallback: str) -> str:
    """Extrai o nome do órgão, código UASG/Unidade e UF do registro do PNCP."""
    orgao_entidade = item.get("orgaoEntidade") or {}
    razao_social = (
        orgao_entidade.get("razaoSocial")
        or item.get("orgaoRazaoSocial")
        or "Órgão Não Informado"
    )

    unidade = item.get("unidadeOrgao") or {}
    uasg = (
        unidade.get("codigoUnidade")
        or item.get("codigoUnidade")
        or item.get("numeroUasg")
        or "N/A"
    )

    uf = (
        unidade.get("ufSigla")
        or item.get("ufSigla")
        or item.get("uf")
        or uf_fallback
    )

    return f"{razao_social} (UASG: {uasg} / UF: {uf})"


def extrair_certame_e_link(item: Dict[str, Any]) -> tuple[str, str]:
    """Extrai o número do certame e constrói o link oficial no Portal PNCP."""
    ano = item.get("anoCompra") or item.get("ano") or ""
    numero = item.get("numeroCompra") or item.get("sequencialCompra") or item.get("numero") or ""
    cnpj = (
        (item.get("orgaoEntidade") or {}).get("cnpj")
        or item.get("cnpjOrgao")
        or ""
    )

    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))

    num_certame = f"{numero}/{ano}" if (numero and ano) else (str(numero) or "S/N")

    # Tenta montar URL direta no PNCP se houver CNPJ, ano e sequencial
    if cnpj_limpo and ano and numero:
        num_seq = str(numero).zfill(6)
        link_oficial = f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{ano}/{num_seq}"
    else:
        link_oficial = item.get("linkSistemaOrigem") or "https://pncp.gov.br"

    return num_certame, link_oficial


def extrair_valor_total(item: Dict[str, Any]) -> str:
    """Extrai e formata o valor total estimado da contratação."""
    val = (
        item.get("valorTotalEstimado")
        or item.get("valorGlobal")
        or item.get("valorTotal")
    )
    if val is not None:
        try:
            val_float = float(val)
            return f"R$ {val_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            pass
    return "Valor não informado"


def objeto_contem_palavras_chave(objeto_texto: str, palavras_chave: List[str]) -> List[str]:
    """Retorna as palavras-chave encontradas no texto do objeto (insensível a acentos e caixa)."""
    texto_norm = remover_acentos(objeto_texto.lower())
    encontradas = []
    for kw in palavras_chave:
        kw_norm = remover_acentos(kw.lower().strip())
        if kw_norm and kw_norm in texto_norm:
            encontradas.append(kw)
    return encontradas


def formatar_objeto_resumido(objeto_texto: str, max_len: int = 120) -> str:
    """Resume o objeto da licitação para exibição no terminal."""
    limpo = " ".join(objeto_texto.split())
    if len(limpo) <= max_len:
        return limpo
    return limpo[: max_len - 3] + "..."


def processar_e_exibir_licitacoes() -> None:
    """Função principal para busca, filtragem e exibição das licitações."""
    print("=" * 80)
    print(" 🤖 ROBÔ DE LICITAÇÕES – JRBC TECH")
    print(" Monitoramento de Contratações Públicas Abertas (PNCP)")
    print("=" * 80)
    print(f"📌 Estados de Interesse: {', '.join(config.estados_interesse)}")
    print(f"🎯 Palavras-chave TI ({len(config.palavras_chave_ti)}): {', '.join(config.palavras_chave_ti)}")
    print("=" * 80)

    client = PNCPClient()
    total_encontradas = 0
    total_filtradas = 0

    for uf in config.estados_interesse:
        print(f"\n🔍 Consultando API do PNCP para a UF: {uf}...")

        try:
            contratacoes = client.buscar_todas_contratacoes_uf(uf=uf)
            print(f"   ↳ {len(contratacoes)} contratação(ões) retornada(s) do PNCP para {uf}.")

            for item in contratacoes:
                total_encontradas += 1
                objeto = extrair_texto_objeto(item)
                kws_encontradas = objeto_contem_palavras_chave(objeto, config.palavras_chave_ti)

                if kws_encontradas:
                    total_filtradas += 1
                    orgao_info = extrair_orgao_uasg_uf(item, uf_fallback=uf)
                    num_certame, link = extrair_certame_e_link(item)
                    objeto_resumido = formatar_objeto_resumido(objeto)
                    valor_total = extrair_valor_total(item)

                    print("\n" + "─" * 80)
                    print(f"🏛️  Órgão / UASG / UF: {orgao_info}")
                    print(f"📄 Certame: {num_certame} | 🔗 Link: {link}")
                    print(f"📝 Objeto: {objeto_resumido}")
                    print(f"🏷️  Keywords TI encontradas: [{', '.join(kws_encontradas)}]")
                    print(f"💰 Valor Total Estimado: {valor_total}")

        except PNCPError as err:
            logger.error(f"Erro ao processar UF {uf}: {err}")
        except Exception as exc:
            logger.error(f"Erro inesperado ao processar UF {uf}: {exc}")

    print("\n" + "=" * 80)
    print(f"📊 RESUMO DO MONITORAMENTO:")
    print(f"   - Total de contratações analisadas: {total_encontradas}")
    print(f"   - Oportunidades de TI filtradas para JRBC TECH: {total_filtradas}")
    print("=" * 80 + "\n")


def main() -> None:
    try:
        processar_e_exibir_licitacoes()
    except KeyboardInterrupt:
        print("\n\n⚠️  Monitoramento interrompido pelo usuário.")
        sys.exit(0)


if __name__ == "__main__":
    main()

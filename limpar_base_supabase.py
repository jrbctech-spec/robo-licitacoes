"""
limpar_base_supabase.py - Saneamento e Faxina da Tabela 'licitacoes' no Supabase
JRBC TECH - CNPJ: 65.488.003/0001-61

Este script audita todos os certames salvos no Supabase e remove em definitivo
qualquer registro fora dos CNAEs estritos da JRBC TECH (saúde, eventos, tendas,
palcos, veículos, merenda, obras civis, papelaria ou compras puras de mercadorias).
"""
import sys
import logging
from pathlib import Path

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LimpezaSupabase")

try:
    from supabase_client import get_client
    from radar_telegram import validar_edital_jrbc
    import cache_manager
except ImportError as e:
    logger.error(f"Erro ao importar módulos do projeto: {e}")
    sys.exit(1)


def executar_saneamento():
    sb = get_client()
    logger.info("Conectado ao Supabase. Buscando licitações para auditoria...")

    # 1. Busca todas as licitações cadastradas
    try:
        res = sb.table("licitacoes").select("id, numero_pregao, orgao_nome, objeto, portal").execute()
        todas = res.data or []
    except Exception as e:
        logger.error(f"Falha ao consultar tabela 'licitacoes': {e}")
        return

    total = len(todas)
    logger.info(f"Total de licitações cadastradas na base: {total}")

    validas = []
    invalidas = []

    for item in todas:
        objeto = item.get("objeto") or ""
        if validar_edital_jrbc(objeto):
            validas.append(item)
        else:
            invalidas.append(item)

    qtd_invalidas = len(invalidas)
    qtd_validas = len(validas)

    logger.info(f"Licitações VÁLIDAS identificadas: {qtd_validas}")
    logger.info(f"Licitações INVÁLIDAS a excluir: {qtd_invalidas}")

    # 2. Exclusão das inválidas em lotes de 50
    if qtd_invalidas > 0:
        ids_excluir = [inv["id"] for inv in invalidas if inv.get("id")]
        tamanho_lote = 50
        total_excluidos = 0

        for i in range(0, len(ids_excluir), tamanho_lote):
            lote = ids_excluir[i : i + tamanho_lote]
            try:
                sb.table("licitacoes").delete().in_("id", lote).execute()
                total_excluidos += len(lote)
                logger.info(f"Progresso de exclusão: {total_excluidos}/{qtd_invalidas} excluídas...")
            except Exception as e:
                logger.error(f"Erro ao excluir lote de IDs ({lote[:3]}...): {e}")

        logger.info(f"✅ Exclusão concluída: {total_excluidos} licitações inválidas removidas.")
    else:
        logger.info("Nenhuma licitação inválida encontrada para exclusão.")

    # 3. Atualizar cache local da aplicação se existir
    try:
        cache = cache_manager.carregar_cache()
        if "todas_licitacoes" in cache:
            cache["todas_licitacoes"] = validas
            cache_manager.salvar_cache("todas_licitacoes", validas)
            logger.info("Cache local atualizado com a base saneada.")
    except Exception as e:
        logger.warning(f"Aviso ao sincronizar cache local: {e}")

    # 4. Resumo final
    print("\n" + "=" * 65)
    print("           RELATÓRIO DE SANEAMENTO - SUPABASE / JRBC TECH")
    print("=" * 65)
    print(f"Total Inicial de Registros: {total}")
    print(f"Total de Registros Inválidos Excluídos: {qtd_invalidas}")
    print(f"Total de Registros Válidos Mantidos:   {qtd_validas}")
    print("=" * 65)
    print("\nExemplos de Certames Válidos Mantidos no Banco:")
    for v in validas[:5]:
        org = (v.get("orgao_nome") or "")[:35]
        np = v.get("numero_pregao") or "S/N"
        obj = (v.get("objeto") or "")[:80]
        print(f"  • [{np}] {org} -> {obj}...")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    executar_saneamento()

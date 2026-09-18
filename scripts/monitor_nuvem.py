"""
monitor_nuvem.py – Script de varredura 24h em nuvem com envio de alertas para o Telegram.

Executa:
  1. Varredura no PNCP para os estados TO e GO filtrando categorias de TI da JRBC TECH.
  2. Aplica regras de precificação e margens (30% Produtos / 50% Serviços) via engine.py.
  3. Filtra duplicatas registradas localmente em JSON ou Supabase.
  4. Envia mensagem formatada ao Telegram via API do Telegram Bot.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

BASE_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from engine import PrecificacaoItem, TipoItemEnum
from pncp_client import buscar_publicacoes_pncp, extrair_params_pncp, minar_e_estruturar_certame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor_nuvem")

UFS_JRBC = ["TO", "GO"]
KEYWORDS_TI_JRBC = ["suporte", "computador", "rede", "software", "ti"]

ARQUIVO_SEEN = BASE_DIR / "data" / "alertas_enviados.json"


def carregar_vistos() -> set[str]:
    """Carrega lista de certames já alertados."""
    if ARQUIVO_SEEN.exists():
        try:
            with open(ARQUIVO_SEEN, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def salvar_vistos(vistos: set[str]) -> None:
    """Salva a lista de certames alertados."""
    ARQUIVO_SEEN.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(ARQUIVO_SEEN, "w", encoding="utf-8") as f:
            json.dump(list(vistos), f, indent=2)
    except Exception as exc:
        logger.warning(f"Erro ao salvar vistos: {exc}")


def enviar_mensagem_telegram(mensagem: str, bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """
    Envia mensagem formatada para o Telegram via API oficial.
    Trata erros de conexão sem travar o processo.
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat:
        logger.info("ℹ️ TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurado. Exibindo mensagem no console:\n" + mensagem)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            logger.info("✅ Alerta enviado com sucesso para o Telegram!")
            return True
        else:
            logger.warning(f"⚠️ Erro no envio do Telegram ({resp.status_code}): {resp.text}")
            return False
    except Exception as exc:
        logger.error(f"🚨 Falha de conexão ao enviar mensagem para o Telegram: {exc}")
        return False


def formatar_alerta_telegram(
    orgao: str,
    uasg: str,
    modalidade: str,
    objeto: str,
    valor_estimado: float,
    tipo_item: TipoItemEnum,
    precificacao: PrecificacaoItem,
    data_sessao: Optional[str],
    url_pncp: str,
) -> str:
    """Formata a mensagem do alerta para o Telegram."""
    margem_txt = "30% (Produtos)" if tipo_item == TipoItemEnum.PRODUTO else "50% (Serviços)"
    data_str = data_sessao or "Verificar no Edital"

    return (
        f"<b>🔔 NOVA OPORTUNIDADE PNCP - JRBC TECH</b>\n\n"
        f"🏛 <b>Órgão:</b> {orgao} (UASG: {uasg})\n"
        f"📋 <b>Modalidade:</b> {modalidade}\n"
        f"📝 <b>Objeto:</b> {objeto[:200]}\n"
        f"💰 <b>Valor Estimado:</b> R$ {valor_estimado:,.2f}\n"
        f"📊 <b>Margem Mínima Sugerida:</b> {margem_txt}\n"
        f"🛡 <b>Piso de Segurança (Mínimo):</b> R$ {precificacao.piso_seguranca:,.2f}\n"
        f"📅 <b>Data/Hora Sessão:</b> {data_str}\n\n"
        f"🔗 <a href='{url_pncp}'>Acessar Edital no PNCP</a>"
    )


def executar_monitoramento_nuvem(dry_run: bool = False) -> int:
    """
    Executa a varredura do PNCP para os estados de TO e GO.
    """
    vistos = carregar_vistos()
    novos_alertas = 0

    logger.info("🔍 Iniciando varredura 24h PNCP (UFs: TO, GO)...")

    for uf in UFS_JRBC:
        for kw in KEYWORDS_TI_JRBC:
            try:
                res = buscar_publicacoes_pncp(uf=uf, palavra_chave=kw, tamanho_pagina=15)
                itens = res.get("data", [])

                for item in itens:
                    cnpj = item.get("orgaoEntidade", {}).get("cnpj", "")
                    ano = item.get("anoCompra", "")
                    seq = item.get("sequencialCompra", "")
                    if not cnpj or not ano or not seq:
                        continue

                    id_unico = f"{cnpj}/{ano}/{seq}"
                    if id_unico in vistos:
                        continue

                    # Determinar tipo de item e margem (produtos 30% / servicos 50%)
                    obj_texto = (item.get("objetoCompra") or "").lower()
                    if any(s in obj_texto for s in ["suporte", "manutencao", "instalacao", "servico", "horas"]):
                        tipo_item = TipoItemEnum.SERVICO
                    else:
                        tipo_item = TipoItemEnum.PRODUTO

                    val_est = float(item.get("valorTotalEstimado") or 0.0)
                    custo_base = val_est * 0.70 if val_est > 0 else 1000.0

                    prec = PrecificacaoItem(
                        tipo_item=tipo_item,
                        custo_base=custo_base,
                        valor_referencia_edital=val_est if val_est > 0 else None,
                    )

                    orgao = item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Desconhecido")
                    uasg = str(item.get("unidadeOrgao", {}).get("codigoUnidade", cnpj[:6]))
                    modalidade = item.get("modalidadeNome", "Dispensa Eletrônica / Pregão")
                    objeto = item.get("objetoCompra", "Objeto não informado")
                    data_sessao = item.get("dataAberturaProposta") or item.get("dataEncerramentoProposta")
                    url_pncp = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"

                    msg_telegram = formatar_alerta_telegram(
                        orgao=orgao,
                        uasg=uasg,
                        modalidade=modalidade,
                        objeto=objeto,
                        valor_estimado=val_est,
                        tipo_item=tipo_item,
                        precificacao=prec,
                        data_sessao=data_sessao,
                        url_pncp=url_pncp,
                    )

                    if not dry_run:
                        enviar_mensagem_telegram(msg_telegram)
                    else:
                        logger.info(f"[DRY-RUN] Alerta gerado para {id_unico}:\n{msg_telegram}")

                    vistos.add(id_unico)
                    novos_alertas += 1

            except Exception as exc:
                logger.warning(f"Erro na busca PNCP [{uf}/{kw}]: {exc}")

    salvar_vistos(vistos)
    logger.info(f"🏁 Varredura concluída. Novos alertas gerados: {novos_alertas}")
    return novos_alertas


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitoramento 24h PNCP em Nuvem com Alertas no Telegram")
    parser.add_argument("--once", action="store_true", help="Executa apenas uma rodada e encerra")
    parser.add_argument("--dry-run", action="store_true", help="Não envia mensagens reais ao Telegram")
    args = parser.parse_args()

    executar_monitoramento_nuvem(dry_run=args.dry_run)

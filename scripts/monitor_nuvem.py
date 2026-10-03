#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/monitor_nuvem.py
Monitor autônomo 24h para o PNCP (Tocantins e Goiás) com alertas executivos no Telegram.
JRBC TECH - CNPJ: 65.488.003/0001-61
"""

import os
import sys
import json
import logging
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
import requests


def remover_acentos(texto: str) -> str:
    """Remove acentos e caracteres especiais para normalização de buscas de texto."""
    if not texto:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MonitorNuvem")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_PNCP_URL = "https://pncp.gov.br/api/consulta/v1"

CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = CACHE_DIR / "certames_notificados.json"

ESTADOS_ALVO = ["TO", "GO"]
PALAVRAS_CHAVE_TI = [
    "suporte", "manutencao", "computador", "notebook", "microcomputador",
    "workstation", "rede", "cabeamento", "fibra", "servidor", "licenca",
    "licenciamento", "software", "saas", "ti", "telecom", "nobreak",
    "switch", "roteador", "helpdesk", "infraestrutura", "ti/telecom"
]

PALAVRAS_SERVICOS = [
    "servico", "manutencao", "suporte", "instalacao", "configuracao",
    "locacao", "treinamento", "consultoria", "implantacao", "operacao"
]


def carregar_historico_notificados() -> set:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return set()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return set(dados.get("notificados", []))
    except Exception as e:
        logger.warning(f"Erro ao carregar cache: {e}. Iniciando vazio.")
        return set()


def salvar_historico_notificados(notificados: set):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"notificados": list(notificados), "ultima_atualizacao": datetime.now().isoformat()}, f, indent=2)
        logger.info(f"Histórico atualizado com {len(notificados)} certames.")
    except Exception as e:
        logger.error(f"Falha ao salvar histórico: {e}")


def classificar_tipo_e_margem(objeto: str) -> dict:
    objeto_norm = remover_acentos(objeto)
    eh_servico = any(remover_acentos(palavra) in objeto_norm for palavra in PALAVRAS_SERVICOS)

    if eh_servico:
        return {
            "tipo": "SERVIÇO",
            "icone": "🛠",
            "margem_percentual": 50.0,
            "fator_piso": 0.50,
            "tag": "#SERVICO"
        }
    else:
        return {
            "tipo": "PRODUTO",
            "icone": "📦",
            "margem_percentual": 30.0,
            "fator_piso": 0.70,
            "tag": "#PRODUTO"
        }


def consultar_pncp_uf(uf: str, data_referencia: datetime) -> list:
    data_str = data_referencia.strftime("%Y%m%d")
    url = f"{BASE_PNCP_URL}/contratacoes/publicacao"
    params = {"dataInicial": data_str, "dataFinal": data_str, "uf": uf, "pagina": 1, "tamanhoPagina": 50, "codigoModalidadeContratacao": 6}
    
    logger.info(f"Consultando PNCP para UF={uf} na data={data_str}...")
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            dados = resp.json()
            return dados.get("data", []) or []
        elif resp.status_code == 404:
            return []
        else:
            return []
    except Exception as e:
        logger.error(f"Erro na conexão com PNCP ({uf}): {e}")
        return []


def enviar_alerta_telegram(certame: dict, precificacao: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Credenciais do Telegram ausentes.")
        return False
        
    orgao = certame.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Não Informado")
    uasg = certame.get("unidadeOrgao", {}).get("codigoUnidade", "N/D")
    uf = certame.get("unidadeOrgao", {}).get("ufSigla", "N/D")
    municipio = certame.get("unidadeOrgao", {}).get("municipioNome", "")
    localidade = f"{municipio} - {uf}" if municipio else uf
    
    modalidade = certame.get("modalidadeNome", "Dispensa/Licitação")
    numero = certame.get("numeroCompra", "")
    ano = certame.get("anoCompra", "")
    numero_processo = f"{numero}/{ano}" if numero and ano else "S/N"
    
    objeto = certame.get("objetoCompra", "Sem descrição informada")
    valor_total = certame.get("valorTotalEstimado") or 0.0
    piso_seguranca = valor_total * precificacao["fator_piso"] if valor_total > 0 else 0.0
    
    data_abertura = certame.get("dataAberturaProposta") or certame.get("dataEncerramentoProposta") or "Consulte o edital"
    if "T" in data_abertura:
        try:
            dt = datetime.fromisoformat(data_abertura.replace("Z", "+00:00"))
            data_abertura = dt.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            pass

    cnpj = certame.get("orgaoEntidade", {}).get("cnpj", "")
    sequencial = certame.get("sequencialCompra", "")
    link_pncp = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}" if cnpj and ano and sequencial else "https://pncp.gov.br"

    tag_estado = f"#{'TOCANTINS' if uf == 'TO' else 'GOIAS' if uf == 'GO' else uf}"
    tag_mod = "#DISPENSA" if "dispensa" in modalidade.lower() else "#PREGAO"

    texto = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛  <b>{orgao.upper()}</b>\n"
        f"📍  {localidade} | <b>UASG:</b> {uasg}\n"
        f"📋  <b>{modalidade} nº {numero_processo}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 <b>OBJETO DA CONTRATAÇÃO:</b>\n"
        f"<i>{objeto[:350]}...</i>\n\n"
        "💰 <b>ANÁLISE DE MARGEM (JRBC TECH):</b>\n"
        f"• <b>Valor Estimado Edital:</b> R$ {valor_total:,.2f}\n"
        f"• <b>Classificação:</b> {precificacao['icone']} {precificacao['tipo']}\n"
        f"• <b>Margem Alvo Líquida:</b> {precificacao['margem_percentual']:.0f}%\n"
        f"• <b>Piso de Lance Seguro:</b> R$ {piso_seguranca:,.2f}\n\n"
        f"📅 <b>ABERTURA DA SESSÃO:</b>\n{data_abertura}\n\n"
        f"🏷 {tag_estado} {tag_mod} #TI {precificacao['tag']}\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "📄 Abrir no PNCP", "url": link_pncp},
                    {"text": "🏛 Portal Compras.gov", "url": "https://www.gov.br/compras"}
                ]
            ]
        }
    }

    try:
        resp = requests.post(url_api, json=payload, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Erro ao enviar Telegram: {e}")
        return False


def main():
    logger.info("=== INICIANDO VARREDURA PNCP (JRBC TECH) ===")
    notificados = carregar_historico_notificados()
    novos = 0
    alertas = 0

    hoje = datetime.now()
    dias = [hoje, hoje - timedelta(days=1)]

    for dia in dias:
        for uf in ESTADOS_ALVO:
            itens = consultar_pncp_uf(uf=uf, data_referencia=dia)
            for item in itens:
                cnpj = item.get("orgaoEntidade", {}).get("cnpj", "")
                ano = item.get("anoCompra", "")
                seq = item.get("sequencialCompra", "")
                id_unico = f"{cnpj}-{ano}-{seq}" if (cnpj and ano and seq) else item.get("numeroControlePNCP")

                if not id_unico or id_unico in notificados:
                    continue

                objeto_norm = remover_acentos(item.get("objetoCompra", ""))
                if any(remover_acentos(palavra) in objeto_norm for palavra in PALAVRAS_CHAVE_TI):
                    novos += 1
                    logger.info(f"Oportunidade encontrada: [{uf}] {item.get('objetoCompra')[:80]}")
                    precificacao = classificar_tipo_e_margem(item.get("objetoCompra", ""))
                    if enviar_alerta_telegram(certame=item, precificacao=precificacao):
                        alertas += 1
                    notificados.add(id_unico)

    salvar_historico_notificados(notificados)
    logger.info(f"Concluído: {novos} avaliados, {alertas} alertas enviados.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/monitor_nuvem.py
Monitor autônomo 24h para o PNCP (Tocantins e Goiás) com alertas no Telegram.
JRBC TECH - CNPJ: 65.488.003/0001-61
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MonitorNuvem")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_PNCP_URL = "https://pncp.gov.br/pncp-api/v1"

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
        logger.info(f"Histórico atualizado com {len(notificados)} certames registrados.")
    except Exception as e:
        logger.error(f"Falha ao salvar histórico: {e}")


def classificar_tipo_e_margem(objeto: str) -> dict:
    objeto_lower = objeto.lower()
    eh_servico = any(palavra in objeto_lower for palavra in PALAVRAS_SERVICOS)
    if eh_servico:
        return {"tipo": "SERVIÇO", "margem_percentual": 50.0, "fator_piso": 0.50}
    else:
        return {"tipo": "PRODUTO", "margem_percentual": 30.0, "fator_piso": 0.70}


def consultar_pncp_uf(uf: str, data_referencia: datetime) -> list:
    data_str = data_referencia.strftime("%Y%m%d")
    url = f"{BASE_PNCP_URL}/consultas/compras/quadro-aviso"
    params = {"dataPublicacao": data_str, "uf": uf, "pagina": 1, "tamanhoPagina": 50}
    
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
    
    objeto = certame.get("objetoCompra", "Sem descrição")
    valor_total = certame.get("valorTotalEstimado") or 0.0
    piso_seguranca = valor_total * precificacao["fator_piso"] if valor_total > 0 else 0.0
    
    data_abertura = certame.get("dataAberturaProposta") or certame.get("dataEncerramentoProposta") or "Ver edital"
    if "T" in data_abertura:
        try:
            dt = datetime.fromisoformat(data_abertura.replace("Z", "+00:00"))
            data_abertura = dt.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            pass

    cnpj = certame.get("orgaoEntidade", {}).get("cnpj", "")
    sequencial = certame.get("sequencialCompra", "")
    link_pncp = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}" if cnpj and ano and sequencial else "https://pncp.gov.br"

    texto = (
        "🚨 *NOVA OPORTUNIDADE IDENTIFICADA — PNCP*\n\n"
        f"🏛 *Órgão:* {orgao}\n"
        f"📍 *Localidade:* {localidade} | *UASG:* {uasg}\n"
        f"📋 *Modalidade:* {modalidade} nº {numero_processo}\n\n"
        f"📝 *Objeto:*\n_{objeto[:300]}..._\n\n"
        f"💰 *Valor Estimado:* R$ {valor_total:,.2f}\n"
        f"🎯 *Margem Alvo:* {precificacao['margem_percentual']:.0f}% ({precificacao['tipo']})\n"
        f"🛡 *Piso de Segurança (Mínimo):* R$ {piso_seguranca:,.2f}\n\n"
        f"📅 *Abertura:* {data_abertura}\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[{"text": "📄 Ver Edital no PNCP", "url": link_pncp}]]
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

                objeto = item.get("objetoCompra", "").lower()
                if any(palavra in objeto for palavra in PALAVRAS_CHAVE_TI):
                    novos += 1
                    precificacao = classificar_tipo_e_margem(item.get("objetoCompra", ""))
                    if enviar_alerta_telegram(certame=item, precificacao=precificacao):
                        alertas += 1
                    notificados.add(id_unico)

    salvar_historico_notificados(notificados)
    logger.info(f"Concluído: {novos} encontrados, {alertas} alertas enviados ao Telegram.")


if __name__ == "__main__":
    main()

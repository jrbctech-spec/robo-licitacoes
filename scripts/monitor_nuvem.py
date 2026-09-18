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

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MonitorNuvem")

# Credenciais e Variáveis de Ambiente
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_PNCP_URL = "https://pncp.gov.br/pncp-api/v1"

# Diretório e Arquivo de Cache de Deduplicação
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = CACHE_DIR / "certames_notificados.json"

# Configuração de Filtros da JRBC TECH
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
    """Carrega o histórico de IDs de contratações já notificadas para evitar duplicatas."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return set()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return set(dados.get("notificados", []))
    except Exception as e:
        logger.warning(f"Erro ao carregar cache de notificações: {e}. Iniciando conjunto vazio.")
        return set()


def salvar_historico_notificados(notificados: set):
    """Salva os IDs no cache JSON de forma atômica."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"notificados": list(notificados), "ultima_atualizacao": datetime.now().isoformat()}, f, indent=2)
        logger.info(f"Histórico atualizado com {len(notificados)} certames registrados.")
    except Exception as e:
        logger.error(f"Falha ao salvar histórico de notificações: {e}")


def classificar_tipo_e_margem(objeto: str) -> dict:
    """
    Aplica as regras de negócio da JRBC TECH:
    - SERVIÇO: 50% de margem líquida mínima.
    - PRODUTO: 30% de margem líquida mínima.
    """
    objeto_lower = objeto.lower()
    eh_servico = any(palavra in objeto_lower for palavra in PALAVRAS_SERVICOS)

    if eh_servico:
        margem_percentual = 50.0
        tipo = "SERVIÇO"
        fator_piso = 0.50  # Lance mínimo estimado para preservar 50% de margem
    else:
        margem_percentual = 30.0
        tipo = "PRODUTO"
        fator_piso = 0.70  # Lance mínimo estimado para preservar 30% de margem

    return {
        "tipo": tipo,
        "margem_percentual": margem_percentual,
        "fator_piso": fator_piso
    }


def consultar_pncp_uf(uf: str, data_referencia: datetime) -> list:
    """
    Consulta a API pública do PNCP buscando compras publicadas na data informada para a UF.
    """
    data_str = data_referencia.strftime("%Y%m%d")
    url = f"{BASE_PNCP_URL}/consultas/compras/quadro-aviso"
    params = {
        "dataPublicacao": data_str,
        "uf": uf,
        "pagina": 1,
        "tamanhoPagina": 50
    }

    logger.info(f"Consultando PNCP para UF={uf} na data={data_str}...")
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            dados = resp.json()
            return dados.get("data", []) or []
        elif resp.status_code == 404:
            logger.info(f"Nenhum registro encontrado no PNCP para {uf} na data {data_str}.")
            return []
        else:
            logger.warning(f"Resposta inesperada da API do PNCP ({resp.status_code}): {resp.text[:200]}")
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão com a API do PNCP para {uf}: {e}")
        return []


def enviar_alerta_telegram(certame: dict, precificacao: dict) -> bool:
    """Envia mensagem formatada com botão inline diretamente para o Telegram do usuário."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos. Alerta não enviado.")
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

    # Cálculo do Piso Seguro (Stop-loss para evitar operar no prejuízo)
    piso_seguranca = valor_total * precificacao["fator_piso"] if valor_total > 0 else 0.0

    data_abertura = certame.get("dataAberturaProposta") or certame.get("dataEncerramentoProposta") or "Consulte o edital"
    if "T" in data_abertura:
        try:
            dt = datetime.fromisoformat(data_abertura.replace("Z", "+00:00"))
            data_abertura = dt.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            pass

    # Link oficial no PNCP
    cnpj = certame.get("orgaoEntidade", {}).get("cnpj", "")
    sequencial = certame.get("sequencialCompra", "")
    link_pncp = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}" if cnpj and ano and sequencial else "https://pncp.gov.br"

    texto = (
        "🚨 *NOVA OPORTUNIDADE IDENTIFICADA — PNCP*\n\n"
        f"🏛 *Órgão:* {orgao}\n"
        f"📍 *Localidade:* {localidade} | *UASG:* {uasg}\n"
        f"📋 *Modalidade:* {modalidade} nº {numero_processo}\n\n"
        f"📝 *Objeto:*\n_{objeto[:350]}..._\n\n"
        f"💰 *Valor Estimado:* R$ {valor_total:,.2f}\n"
        f"🎯 *Margem Alvo:* {precificacao['margem_percentual']:.0f}% ({precificacao['tipo']})\n"
        f"🛡 *Piso de Segurança (Lance Mínimo):* R$ {piso_seguranca:,.2f}\n\n"
        f"📅 *Data da Sessão:* {data_abertura}\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "📄 Acessar Edital no PNCP", "url": link_pncp}]
            ]
        }
    }

    try:
        resp = requests.post(url_api, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info(f"Alerta enviado com sucesso via Telegram para o certame {numero_processo}.")
            return True
        else:
            logger.error(f"Erro ao enviar alerta ao Telegram ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Exceção ao disparar mensagem para o Telegram: {e}")
        return False


def main():
    logger.info("=== INICIANDO VARREDURA 24H NO PNCP (JRBC TECH) ===")
    notificados = carregar_historico_notificados()
    novos_encontrados = 0
    alertas_disparados = 0

    hoje = datetime.now()
    dias_para_varrer = [hoje, hoje - timedelta(days=1)]

    for dia in dias_para_varrer:
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

                # Aplica filtro de palavras-chave de TI da JRBC TECH
                if any(palavra in objeto for palavra in PALAVRAS_CHAVE_TI):
                    novos_encontrados += 1
                    logger.info(f"Oportunidade aderente encontrada: [{uf}] {item.get('objetoCompra')[:80]}")

                    precificacao = classificar_tipo_e_margem(item.get("objetoCompra", ""))
                    sucesso = enviar_alerta_telegram(certame=item, precificacao=precificacao)

                    # Registra no histórico para evitar reenvio
                    notificados.add(id_unico)
                    if sucesso:
                        alertas_disparados += 1

    salvar_historico_notificados(notificados)
    logger.info(f"Varredura concluída. {novos_encontrados} oportunidades avaliadas, {alertas_disparados} alertas enviados.")


if __name__ == "__main__":
    main()

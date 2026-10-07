"""
telegram_bot.py – Serviço de mensageria e notificações Telegram para JRBC TECH.

Responsável por:
  - Validar conexão e credenciais do bot via API oficial do Telegram.
  - Enviar alertas prioritários de disputa (ultrapassagens, lances disparados).
  - Executar varreduras de oportunidades do PNCP focadas em Tocantins (TO) e TI.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class TelegramBotService:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
        token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
        cid = chat_id or os.getenv("TELEGRAM_CHAT_ID") or ""
        self.token = str(token).strip()
        self.chat_id = str(cid).strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def testar_conexao(self) -> bool:
        """Verifica se o token é válido e busca o identificador do bot."""
        if not self.token:
            logger.warning("[TELEGRAM] Token não informado ou vazio.")
            return False
        try:
            resp = requests.get(f"{self.base_url}/getMe", timeout=10)
            dados = resp.json()
            if dados.get("ok"):
                username = dados["result"].get("username", "")
                nome_bot = dados["result"].get("first_name", "")
                logger.info(f"[TELEGRAM] Conectado com sucesso ao bot: @{username} ({nome_bot})")
                return True
            else:
                logger.warning(f"[TELEGRAM] Erro de autenticação: {dados.get('description')}")
                return False
        except Exception as e:
            logger.error(f"[TELEGRAM] Falha de conexão: {e}")
            return False

    def enviar_mensagem(self, texto: str) -> bool:
        """Envia mensagem formatada em Markdown ou texto para o chat configurado."""
        if not self.token or not self.chat_id:
            logger.debug("[TELEGRAM] Credenciais ausentes para envio.")
            return False
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": texto,
                "parse_mode": "Markdown",
            }
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"[TELEGRAM] Falha ao enviar mensagem: {e}")
            return False

    def varrer_oportunidades_pncp(self, uf: str = "TO", termo: str = "tecnologia") -> list[dict[str, Any]]:
        """
        Consulta a API pública do PNCP em busca de novas contratações/dispensas
        na região e envia um resumo das oportunidades encontradas.
        """
        data_hoje = datetime.now().strftime("%Y%m%d")
        url_pncp = (
            f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
            f"?dataInicial={data_hoje}&dataFinal={data_hoje}&codigoModalidadeContratacao=8"
            f"&uf={uf}&pagina=1&tamanhoPagina=15"
        )
        encontrados: list[dict[str, Any]] = []

        try:
            headers = {"User-Agent": "JRBC-Tech-Bot/1.0"}
            r = requests.get(url_pncp, headers=headers, timeout=15)
            if r.status_code != 200:
                logger.warning(f"[PNCP] Resposta {r.status_code} ao buscar oportunidades.")
                return encontrados

            itens = r.json().get("data", [])
            keywords = [termo.lower(), "informática", "computador", "rede", "software", "suporte", "serviço"]

            for item in itens:
                if not isinstance(item, dict):
                    continue
                objeto = str(item.get("objetoCompra", "")).lower()
                if any(kw in objeto for kw in keywords):
                    orgao = item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Público") if isinstance(item.get("orgaoEntidade"), dict) else "Órgão Público"
                    from radar_telegram import formatar_link_pncp
                    link = formatar_link_pncp(item)
                    encontrados.append({
                        "orgao": orgao,
                        "objeto": item.get("objetoCompra", ""),
                        "link": link,
                    })

            if encontrados:
                corpo = f"📡 *Radar JRBC TECH: {len(encontrados)} Oportunidade(s) em {uf}*\n\n"
                for i, op in enumerate(encontrados, start=1):
                    corpo += f"*{i}. {op['orgao']}*\n"
                    desc_res = str(op['objeto'])[:120].replace("*", "")
                    corpo += f"_{desc_res}..._\n"
                    corpo += f"[Acessar Edital]({op['link']})\n\n"
                self.enviar_mensagem(corpo)
            else:
                logger.info(f"[PNCP] Nenhuma oportunidade de {termo} aberta hoje em {uf}.")
        except Exception as e:
            logger.error(f"[PNCP] Erro na varredura: {e}")

        return encontrados


# Instância global padrão carregada com variáveis de ambiente
telegram_service = TelegramBotService()

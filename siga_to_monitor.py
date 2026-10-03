"""
siga_to_monitor.py - Monitor de Compras Diretas / Dispensas Eletrônicas (SIGA-TO)
Foco exclusivo em Prestação de Serviços (TI, Infraestrutura, Redes, Suporte N1).

Integração direta com o bot do Telegram existente no projeto robo-licitacoes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from playwright.async_api import async_playwright

from supabase_client import inserir_licitacao

# Garante o carregamento do .env local
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SIGA_Monitor")

# ─────────────────────────────────────────────────────────────────────────────
# Configurações do Telegram e Persistência
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
VISTOS_FILE = Path("data/siga_processos_vistos.json")

# URL oficial da Compra Direta Eletrônica do Tocantins
SIGA_ABERTAS_URL = "http://siga.to.gov.br/cde/#/fornecedor/abertas"

# ─────────────────────────────────────────────────────────────────────────────
# Regras de Negócio: Filtro de Serviços Técnicos de TI / Infra
# ─────────────────────────────────────────────────────────────────────────────

# Palavras-chave que indicam serviços de seu interesse
KEYWORDS_SERVICOS = [
    "prestação de serviço", "prestacao de servico", "prestação de serviços",
    "manutenção", "manutencao", "suporte técnico", "suporte",
    "cabeamento", "cabeamento estruturado", "ponto de rede", "pontos de rede",
    "fibra óptica", "fibra optica", "infraestrutura de rede",
    "computador", "computadores", "microcomputador", "microcomputadores",
    "notebook", "notebooks", "servidor", "servidores",
    "nobreak", "nobreaks", "estabilizador",
    "impressora", "impressoras", "recarga de toner",
    "instalação", "instalacao", "configuração", "configuracao",
    "cftv", "câmeras", "cameras", "controle de acesso",
    "roteador", "switch", "switches", "rack"
]

# Blacklist: termos para descartar automaticamente compras fora da sua área
BLACKLIST_TERMOS = [
    "limpeza", "conservação", "conservacao", "vigilância", "vigilancia",
    "portaria", "reforma predial", "alvenaria", "pintura predial",
    "elétrica predial", "engenharia civil", "mecânica", "mecanica",
    "veículo", "veiculo", "veículos", "pneu", "pneus",
    "combustível", "combustivel", "merenda", "alimentação", "alimentacao",
    "buffet", "coffee break", "gráfico", "grafica",
    "ar condicionado", "climatização", "climatizacao", "dedetização"
]


class TelegramDispatcher:
    """Despachante de mensagens para o Bot do Telegram já existente."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def enviar_oportunidade(
        self,
        numero_processo: str,
        orgao: str,
        objeto: str,
        data_fim: str,
        link_processo: str,
        termo_encontrado: str,
    ) -> bool:
        if not self.token or not self.chat_id:
            logger.warning("Credenciais do Telegram não configuradas no .env.")
            return False

        mensagem_html = (
            "🌴 **[OPORTUNIDADE DE SERVIÇO - SIGA TO]**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 **Órgão:** {orgao}\n"
            f"📄 **Processo:** `{numero_processo}`\n"
            f"⏰ **Encerramento:** `{data_fim}`\n"
            f"🔑 **Termo compatível:** *{termo_encontrado}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **Objeto:**\n{objeto[:400]}{'...' if len(objeto) > 400 else ''}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": mensagem_html,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "🔗 Acessar no SIGA-TO", "url": link_processo}]
                ]
            },
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(self.api_url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info(f"✅ Alerta enviado no Telegram para o processo {numero_processo}")
                return True
            else:
                logger.error(f"Erro ao enviar no Telegram ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Exceção ao disparar Telegram: {e}")
            return False


class SigaMonitor:
    """Monitor de compras públicas do SIGA-TO via Playwright."""

    def __init__(self):
        self.telegram = TelegramDispatcher(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.processos_vistos = self._carregar_vistos()

    def _carregar_vistos(self) -> set[str]:
        VISTOS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if VISTOS_FILE.exists():
            try:
                with open(VISTOS_FILE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _salvar_vistos(self) -> None:
        with open(VISTOS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(self.processos_vistos), f, ensure_ascii=False, indent=2)

    def _salvar_supabase(self, num_proc: str, orgao: str, objeto: str, data_fim: str, link: str):
        dados_licitacao = {
            "numero_uasg": "SIGA-TO",  # Evita conflitos com UASGs numéricas
            "numero_pregao": num_proc,
            "orgao": orgao,
            "objeto": objeto[:2000],
            "modalidade": "Compra Direta Estadual (SIGA-TO)",
            "uf": "TO",
            "municipio": "Palmas/TO",
            "link_sistema": link,
            "status_analise": "pendente",
            "portal": "SIGA_TO"
        }
        
        # Tenta formatar data se for DD/MM/YYYY HH:MM
        match_prazo = re.search(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', data_fim)
        if match_prazo:
            try:
                dt = datetime.strptime(match_prazo.group(0), "%d/%m/%Y %H:%M")
                dados_licitacao["data_fim_proposta"] = dt.isoformat()
            except Exception:
                pass
                
        try:
            inserir_licitacao(dados_licitacao)
            logger.info(f"💾 Compra SIGA {num_proc} salva no Supabase.")
        except Exception as e:
            logger.error(f"Erro ao salvar no Supabase: {e}")

    def classificar_objeto(self, texto: str) -> Optional[str]:
        """Avalia se o objeto corresponde a serviços de TI e não cai em blacklist."""
        texto_lower = texto.lower()

        # Checa termos negativos
        for black in BLACKLIST_TERMOS:
            if re.search(r"\b" + re.escape(black) + r"\b", texto_lower):
                return None

        # Checa termos positivos
        for kw in KEYWORDS_SERVICOS:
            if kw in texto_lower:
                return kw

        return None

    async def executar_varredura(self) -> int:
        """Abre o SIGA-TO, intercepta os processos abertos e envia alertas."""
        novas_oportunidades = 0
        logger.info(f"Iniciando varredura no SIGA-TO: {SIGA_ABERTAS_URL}")

        async with async_playwright() as p:
            import chrome_connector
            context, is_cdp = await chrome_connector.obter_contexto_navegador(p)
            page = await chrome_connector.obter_aba_inteligente(context, "siga.to.gov.br")

            # Captura de respostas JSON da API do portal
            dados_capturados_api: List[Dict[str, Any]] = []

            async def interceptar_resposta(response):
                url = response.url
                if ("consulta" in url or "compra" in url or "abertas" in url) and "json" in response.headers.get("content-type", ""):
                    try:
                        res_json = await response.json()
                        if isinstance(res_json, list):
                            dados_capturados_api.extend(res_json)
                        elif isinstance(res_json, dict) and "itens" in res_json:
                            dados_capturados_api.extend(res_json["itens"])
                        elif isinstance(res_json, dict) and "data" in res_json:
                            dados_capturados_api.extend(res_json["data"])
                    except Exception:
                        pass

            page.on("response", interceptar_resposta)

            try:
                await page.goto(SIGA_ABERTAS_URL, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(5000)  # Garante renderização Angular/Vue
            except Exception as e:
                logger.warning(f"Timeout ao carregar página: {e}. Prosseguindo com leitura...")
            
            logger.info(f'URL atual: {page.url}')

            # 1. Triagem via dados de API interceptados (se houver)
            if dados_capturados_api:
                logger.info(f"Interceptados {len(dados_capturados_api)} registros via API.")
                for item in dados_capturados_api:
                    num_proc = str(item.get("numeroProcesso") or item.get("numero") or item.get("id") or "")
                    objeto = str(item.get("objeto") or item.get("descricao") or "")
                    orgao = str(item.get("orgao") or item.get("nomeOrgao") or "Governo do Tocantins")
                    data_fim = str(item.get("dataEncerramento") or item.get("dataFim") or "A consultar")

                    if not num_proc or num_proc in self.processos_vistos:
                        continue

                    termo = self.classificar_objeto(objeto)
                    if termo:
                        link = f"http://siga.to.gov.br/cde/#/fornecedor/detalhes/{num_proc}"
                        enviado = self.telegram.enviar_oportunidade(
                            numero_processo=num_proc,
                            orgao=orgao,
                            objeto=objeto,
                            data_fim=data_fim,
                            link_processo=link,
                            termo_encontrado=termo,
                        )
                        if enviado:
                            self._salvar_supabase(num_proc, orgao, objeto, data_fim, link)
                            self.processos_vistos.add(num_proc)
                            novas_oportunidades += 1

            # 2. Triagem via DOM / Tabela visual
            linhas = await page.query_selector_all("table tbody tr")
            logger.info(f"Encontradas {len(linhas)} linhas na tabela visual.")
            if len(linhas) == 0:
                await page.screenshot(path='debug_siga.png')
                logger.info("Screenshot de debug salvo em debug_siga.png")

            for tr in linhas:
                texto_linha = await tr.inner_text()
                colunas = texto_linha.split("\t") if "\t" in texto_linha else texto_linha.split("\n")
                colunas = [c.strip() for c in colunas if c.strip()]

                if len(colunas) >= 3:
                    # Heurística para identificar processo e objeto nas colunas
                    num_proc = colunas[0]
                    orgao = colunas[1] if len(colunas) > 1 else "Órgão Estadual"
                    objeto = " ".join(colunas[2:])

                    # Extrai número de processo padrão YYYY/NNNNN se presente
                    match_proc = re.search(r"\d{4}/\d{4,6}", texto_linha)
                    if match_proc:
                        num_proc = match_proc.group(0)

                    if num_proc in self.processos_vistos:
                        continue

                    termo = self.classificar_objeto(objeto)
                    if termo:
                        data_fim_match = re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", texto_linha)
                        data_fim = data_fim_match.group(0) if data_fim_match else "Verificar no edital"

                        enviado = self.telegram.enviar_oportunidade(
                            numero_processo=num_proc,
                            orgao=orgao,
                            objeto=objeto,
                            data_fim=data_fim,
                            link_processo=SIGA_ABERTAS_URL,
                            termo_encontrado=termo,
                        )
                        if enviado:
                            self._salvar_supabase(num_proc, orgao, objeto, data_fim, SIGA_ABERTAS_URL)
                            self.processos_vistos.add(num_proc)
                            novas_oportunidades += 1

            if context:
                await context.close()
        self._salvar_vistos()
        logger.info(f"Varredura concluída. Novas oportunidades notificadas: {novas_oportunidades}")
        return novas_oportunidades


# ─────────────────────────────────────────────────────────────────────────────
# Execução Principal
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    monitor = SigaMonitor()

    # Se chamado com argumento --loop, executa a cada 20 minutos
    if "--loop" in sys.argv:
        logger.info("Modo contínuo ativado (verificação a cada 20 minutos).")
        while True:
            try:
                await monitor.executar_varredura()
            except Exception as e:
                logger.error(f"Erro na iteração do monitor: {e}")
            await asyncio.sleep(20 * 60)
    else:
        # Execução única
        await monitor.executar_varredura()


if __name__ == "__main__":
    asyncio.run(main())

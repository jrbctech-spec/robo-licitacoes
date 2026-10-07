import os
import logging
import asyncio
import requests
from playwright.async_api import async_playwright, BrowserContext, Page
from typing import Tuple, Optional

logger = logging.getLogger("ChromeConnector")

CDP_URL = "http://localhost:9222"
FALLBACK_PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "browser_profile"))


def is_chrome_running() -> bool:
    """Verifica se o Chrome oficial está respondendo na porta 9222."""
    try:
        resp = requests.get(f"{CDP_URL}/json/version", timeout=1)
        if resp.status_code == 200:
            return True
    except requests.RequestException:
        pass
    return False


async def obter_contexto_navegador(playwright) -> Tuple[Optional[BrowserContext], bool]:
    """
    Retorna o contexto do navegador conectado via CDP, ou o contexto persistente de fallback.
    O booleano indica se conectou via CDP (True) ou Fallback (False).
    """
    if is_chrome_running():
        try:
            logger.info("🔌 Conectando ao Chrome oficial via CDP na porta 9222...")
            browser = await playwright.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            return context, True
        except Exception as e:
            logger.error(f"Falha ao conectar via CDP: {e}")
            
    # Fallback
    logger.info(f"⚪ Chrome oficial não encontrado na porta 9222. Abrindo perfil persistente em {FALLBACK_PROFILE_DIR}...")
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=FALLBACK_PROFILE_DIR,
        headless=False,
        args=['--no-sandbox', '--disable-setuid-sandbox']
    )
    return context, False


async def obter_aba_inteligente(context: BrowserContext, url_alvo: str) -> Page:
    """
    Verifica se já existe uma aba aberta com a URL alvo (ex: siga.to.gov.br).
    Retorna essa aba ou cria uma nova.
    """
    for page in context.pages:
        if url_alvo.lower() in page.url.lower():
            logger.info(f"🎯 Aba inteligente detectada: aproveitando aba existente para {url_alvo}")
            return page
            
    logger.info(f"📄 Nenhuma aba aberta para {url_alvo}. Abrindo nova aba.")
    new_page = await context.new_page()
    return new_page

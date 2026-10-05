import asyncio
import logging
from typing import Optional, List
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger("NavegadorGestor")

class NavegadorGestor:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None

    async def iniciar(self) -> None:
        """Inicializa ou conecta ao navegador via porta 9222."""
        self.playwright = await async_playwright().start()
        
        try:
            # Tenta conectar via CDP (navegador já aberto pelo usuário)
            self.browser = await self.playwright.chromium.connect_over_cdp("http://localhost:9222", timeout=3000)
            self.context = self.browser.contexts[0]
            logger.info("✅ Conectado ao Chrome via porta 9222.")
        except Exception:
            logger.warning("⚠️ Não foi possível conectar na porta 9222. Abrindo novo navegador...")
            # Lança novo navegador
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=Path("./browser_profile_multi"),
                headless=False,
                slow_mo=100,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
            )
            logger.info("✅ Novo navegador Chromium iniciado.")

    async def obter_pagina_para_url(self, url: str) -> Page:
        """
        Garante que retorna uma página navegada para a URL desejada.
        Não deixa páginas paradas em 'about:blank'.
        """
        if not self.context:
            await self.iniciar()

        if not self.context:
            raise RuntimeError("Não foi possível inicializar o contexto do navegador.")

        # Verifica se já tem alguma página na mesma url (ou parte dela)
        for page in self.context.pages:
            if "about:blank" not in page.url and url in page.url:
                await page.bring_to_front()
                return page

        # Verifica se há alguma aba vazia para reutilizar
        page_alvo = None
        for page in self.context.pages:
            if page.url == "about:blank":
                page_alvo = page
                break

        if not page_alvo:
            page_alvo = await self.context.new_page()

        # Navega obrigatoriamente para a URL alvo
        try:
            logger.info(f"Navegando para: {url[:60]}...")
            await page_alvo.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page_alvo.wait_for_timeout(3000)
        except Exception as e:
            logger.warning(f"Aviso de navegação (pode estar carregado mas deu timeout): {e}")

        return page_alvo

    async def fechar(self) -> None:
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

# Instância global Singleton
_gestor = NavegadorGestor()

async def obter_gestor() -> NavegadorGestor:
    if not _gestor.context:
        await _gestor.iniciar()
    return _gestor

import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

def executar_varredura_siga():
    """Varre o portal SIGA-TO Compra Direta buscando contratações usando perfil persistente."""
    logger.info("Iniciando varredura no SIGA-TO Compra Direta...")
    perfil = Path(__file__).resolve().parent / 'browser_profile_siga'
    
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(perfil),
                headless=False
            )
            page = ctx.new_page()
            url = 'https://www.siga.to.gov.br/compra-direta/#/selecionar-dominio'
            
            logger.info(f"Navegando para {url}")
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            logger.info("Acessou SIGA-TO Compra Direta com sucesso usando perfil persistente.")
            
            # TODO: Implementar lógica de extração de itens e chamadas Supabase aqui
            
            # Delay rápido
            page.wait_for_timeout(3000)
            
            ctx.close()
            logger.info("Varredura SIGA-TO concluída.")
            
    except Exception as e:
        logger.error(f"Erro durante scraping do SIGA-TO Compra Direta: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    executar_varredura_siga()

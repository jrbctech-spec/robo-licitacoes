"""
Módulo: siga_monitor.py
Finalidade: Monitoramento do SIGA-TO (Governo do Tocantins) usando Playwright.
"""

import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger("SigaMonitor")

TERMOS_TI = [
    "informatica", "computador", "notebook", "impressora", "suporte",
    "manutencao", "cabeamento", "rede", "toner", "cartucho", "locacao",
    "help desk", "servidor", "estacao de trabalho", "telefonia"
]

def varrer_siga_to() -> list[dict]:
    """Varre o portal SIGA-TO buscando contratações de TI."""
    oportunidades = []
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            url = "https://siga.to.gov.br/" 
            
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                # O scraping exato do SIGA-TO depende de navegar pelo portal.
                # Aqui estruturamos a base do coletor. É possível inserir locators específicos
                # (ex: page.locator(".card-licitacao").all()) conforme o layout da página.
                
                logger.info("Acessou SIGA-TO com sucesso. (Implementar locators específicos)")
                
                # Exemplo de mock para não quebrar o fluxo:
                # oportunidades.append({
                #    "origem": "SIGA-TO",
                #    "esfera": "ESTADUAL",
                #    "orgao_nome": "Governo do Tocantins",
                #    "uf": "TO",
                #    "numero_processo": "S/N",
                #    "modalidade": "Dispensa Eletrônica",
                #    "objeto": "Contratação de TI",
                #    "valor_estimado": 0.0,
                #    "link_edital": url,
                #    "id_unico": "SIGATO-MOCK-1"
                # })

            except Exception as e:
                logger.error(f"Erro durante scraping do SIGA-TO: {e}")
                
            finally:
                browser.close()
            
    except Exception as e:
        logger.error(f"Erro ao inicializar Playwright para SIGA-TO: {e}")

    return oportunidades

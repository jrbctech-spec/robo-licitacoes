from playwright.sync_api import sync_playwright
from pathlib import Path

perfil = Path(__file__).resolve().parent / 'browser_profile_siga'

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(perfil), headless=False)
    page = ctx.new_page()
    page.goto('https://www.siga.to.gov.br/compra-direta/#/selecionar-dominio')
    print('Faça seu login no navegador. Quando terminar, pressione Enter aqui...')
    input()
    ctx.close()

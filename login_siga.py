from playwright.sync_api import sync_playwright
from pathlib import Path

perfil = Path(__file__).resolve().parent / "browser_profile_siga"
perfil.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(perfil), headless=False)
    page = ctx.new_page()
    page.goto("https://www.siga.to.gov.br/compra-direta/#/selecionar-dominio")
    print("\n" + "="*60)
    print("👉 O navegador foi aberto na tela do SIGA-TO!")
    print("👉 Faça o seu login de fornecedor normalmente.")
    print("👉 Quando estiver conectado na página principal, volte aqui e aperte ENTER.")
    print("="*60 + "\n")
    input("Pressione ENTER após concluir o login no navegador...")
    ctx.close()
    print("✅ Sessão salva com sucesso em browser_profile_siga!")

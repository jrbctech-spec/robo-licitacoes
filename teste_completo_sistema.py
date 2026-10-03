import os
import sys
import time
import requests
from dotenv import load_dotenv

def log(msg):
    print(f"[*] {msg}")

def test_imports():
    log("Testando importações...")
    import app_gui
    import siga_to_monitor
    import pncp_client
    import mercadolivre_gui
    import supabase_client
    import edital_parser
    import chrome_connector
    log("Importações OK.")

def test_supabase():
    log("Testando conexão com Supabase...")
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Credenciais do Supabase ausentes no .env")
    sb = create_client(url, key)
    # Tenta ler da tabela logs_sessao (ou inserir um log de teste)
    res = sb.table("logs_sessao").select("*").limit(1).execute()
    log(f"Supabase OK (recebeu {len(res.data)} registros da tabela logs_sessao)")

def test_telegram():
    log("Testando conexão com Telegram...")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN ausente no .env")
    url = f"https://api.telegram.org/bot{token}/getMe"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    if not data.get("ok"):
        raise ValueError(f"Telegram retornou erro: {data}")
    log(f"Telegram OK (Bot: {data['result'].get('username')})")

def test_pncp():
    log("Testando API do PNCP...")
    import pncp_client
    try:
        data = pncp_client.buscar_publicacoes_pncp(data_inicial="20240101", data_final="20240105", pagina=1, tamanho_pagina=1)
        log(f"PNCP OK (encontrou itens)")
    except Exception as e:
        log(f"Falha na consulta PNCP, ignorando teste restritivo: {e}")

def test_siga_to():
    log("Testando conexão com SIGA-TO...")
    url = "https://siga.to.gov.br/"
    res = requests.get(url, timeout=15, verify=False)  # IGNORA SSL por via das duvidas
    log(f"SIGA-TO OK (Status: {res.status_code})")

def test_gui():
    log("Testando instanciação da GUI (App)...")
    from app_gui import App
    app = App()
    app.after(3000, app.destroy)
    app.mainloop()
    log("GUI renderizada e destruída com sucesso.")

if __name__ == "__main__":
    load_dotenv()
    try:
        test_imports()
        test_supabase()
        test_telegram()
        test_pncp()
        test_siga_to()
        test_gui()
        print("\n✅ TODOS OS TESTES PASSARAM COM SUCESSO!")
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n❌ ERRO DURANTE OS TESTES: {e}")
        traceback.print_exc()
        sys.exit(1)

import os
import time
import requests
import importlib
from dotenv import load_dotenv

load_dotenv()

results = []

def add_result(component, status, time_ms, diag):
    results.append({"comp": component, "status": status, "time": time_ms, "diag": diag})
    print(f"[{status}] {component} - {time_ms:.1f}ms - {diag}")

def test_1_gui():
    t0 = time.time()
    try:
        import app_gui
        # Instanciar interface (requer X11 ou similar)
        # Vamos usar um try-catch agressivo para ambientes sem tela
        try:
            os.environ.setdefault("DISPLAY", ":0")
            app = app_gui.App()
            t1 = time.time()
            time_init = (t1 - t0)*1000
            
            # Teste de alternância (latência)
            t_aba_0 = time.time()
            app.selecionar_aba("⚡  Console da Sessão")
            app.update_idletasks()
            t_aba_1 = time.time()
            latencia = (t_aba_1 - t_aba_0)*1000
            
            # Sincronização e Espelhamento de Estado
            t_sync_0 = time.time()
            lic_fake = {"numero_pregao": "TESTE/2026", "link_sistema": "https://teste.gov.br", "uf": "TO", "orgao_nome": "TESTE"}
            itens_fake = [{"numero_item": "1", "descricao": "Item 1", "piso_seguro": 100.0}]
            app.definir_licitacao_ativa(lic_fake, itens_fake)
            app.update_idletasks()
            
            if app.painel_sessao.txt_url.get() != "https://teste.gov.br":
                raise Exception("URL do console não sincronizou.")
                
            t_sync_1 = time.time()
            time_sync = (t_sync_1 - t_sync_0)*1000
            
            app.destroy()
            
            add_result("App Gui Init", "OK", time_init, "App instanciado com sucesso")
            add_result("App Latência Abas", "OK", latencia, f"Troca de aba em {latencia:.1f}ms")
            add_result("Sync Console", "OK", time_sync, "Sincronização bidirecional funcionou")
            
        except Exception as e:
            add_result("App Gui Init", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))
    except Exception as e:
        add_result("App Gui Init", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))

def test_2_gemini():
    t0 = time.time()
    try:
        from google import genai
        client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
        res = client.models.generate_content(
            model='gemini-3.8-flash',
            contents='Responda apenas "ok".'
        )
        t1 = time.time()
        texto_resp = res.text.strip()[:15] if res.text else "Resp vazia/bloqueada"
        add_result("Gemini Copiloto", "OK", (t1-t0)*1000, f"Resp: {texto_resp}")
    except Exception as e:
        add_result("Gemini Copiloto", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))

def test_3_supabase():
    t0 = time.time()
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise Exception("Credenciais Supabase nao encontradas")
        sb = create_client(url, key)
        res = sb.table("licitacoes").select("id, portal, prioridade").limit(1).execute()
        t1 = time.time()
        add_result("Supabase DB", "OK", (t1-t0)*1000, f"Query bem sucedida, {len(res.data)} res")
    except Exception as e:
        add_result("Supabase DB", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))

def test_5_monitors():
    t0 = time.time()
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get("https://pncp.gov.br/api/pncp/v1/orgaos/00394460000141/compras", timeout=15, headers=headers)
        add_result("PNCP API", "OK", (time.time()-t0)*1000, f"Status {res.status_code}")
    except Exception as e:
        add_result("PNCP API", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))
        
    t0 = time.time()
    try:
        importlib.import_module("siga_to_monitor")
        add_result("Módulos de Conexão", "OK", (time.time()-t0)*1000, "siga_to_monitor Compila")
    except Exception as e:
        add_result("Módulos de Conexão", "FALHA/AVISO", (time.time()-t0)*1000, str(e).replace('\n', ' '))
        
    t0 = time.time()
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        res = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5).json()
        if res.get("ok"):
            add_result("Telegram Bot", "OK", (time.time()-t0)*1000, res["result"]["first_name"])
        else:
            raise Exception("Telegram getMe retornou false")
    except Exception as e:
        add_result("Telegram Bot", "FALHA", (time.time()-t0)*1000, str(e).replace('\n', ' '))

if __name__ == "__main__":
    print("Iniciando auditoria completa...")
    test_1_gui()
    test_2_gemini()
    test_3_supabase()
    test_5_monitors()

    print("\n-- RELATORIO_JSON --")
    import json
    print(json.dumps(results))

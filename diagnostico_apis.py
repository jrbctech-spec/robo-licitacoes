import os
import requests
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def testar_conexoes():
    print("=== DIAGNÓSTICO DE CONEXÕES DO ROBÔ ===")
    
    # 1. Testa PNCP (Governo)
    try:
        headers = {
            "User-Agent": "JRBC-Tech-Licitacoes/1.0 (contato@jrbctech.com.br)",
            "Accept": "application/json"
        }
        # Parâmetros exatos que usamos no buscador para não dar Erro 400
        url = "https://pncp.gov.br/api/search/"
        params = {"q": "informatica", "tipos_documento": "edital", "status": "aberto", "pagina": 1, "tam_pagina": 1}
        
        r = requests.get(url, params=params, headers=headers, timeout=10)
        print('✓ Conexão Externa PNCP:', 'OK' if r.status_code == 200 else f'Falhou (Status: {r.status_code}) - {r.text[:100]}')
    except Exception as e:
        print('! Erro no PNCP:', e)

    # Pega da variável de ambiente OU usa a URL padrão oficial da JRBC TECH
    url = os.environ.get("SUPABASE_URL") or "https://szphoxostihnpcovyzvx.supabase.co"
    key = os.environ.get("SUPABASE_KEY")
    
    if key == "COLE_AQUI_SUA_CHAVE_DO_SUPABASE":
        print("! Supabase: Chave não encontrada. Por favor, edite o arquivo .env com a sua SUPABASE_KEY ou rode: export SUPABASE_KEY='sua_chave'")
    else:
        try:
            sb = create_client(url, key)
            res = sb.table('licitacoes').select('*', count='exact').limit(1).execute()
            print('✓ Conexão Externa Supabase: OK (Registros no banco:', res.count, ')')
        except Exception as e:
            print('! Erro no Supabase:', e)

if __name__ == "__main__":
    testar_conexoes()

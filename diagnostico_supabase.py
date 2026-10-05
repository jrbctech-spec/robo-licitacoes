import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from postgrest.types import CountMethod
from collections import Counter

def main():
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("Erro: Credenciais do Supabase não encontradas no .env")
        sys.exit(1)
        
    sb = create_client(url, key)
    
    print("\n=== 1. ESTRUTURA E COLUNAS ===")
    try:
        res = sb.table("licitacoes").select("*").limit(1).execute()
        if res.data and isinstance(res.data[0], dict):
            colunas = list(res.data[0].keys())
            print(f"Colunas encontradas: {len(colunas)} colunas.")
            print(f" -> Coluna 'portal' existe? {'✅ Sim' if 'portal' in colunas else '❌ NÃO'}")
            print(f" -> Coluna 'prioridade' existe? {'✅ Sim' if 'prioridade' in colunas else '❌ NÃO'}")
        else:
            print("Tabela 'licitacoes' está vazia, impossível ler as colunas pelo select simples.")
    except Exception as e:
        print(f"Erro ao acessar estrutura: {e}")

    print("\n=== 2. CONTAGEM E DUPLICIDADES ===")
    tabelas = ["licitacoes", "itens_licitacao", "logs_sessao"]
    for t in tabelas:
        try:
            res = sb.table(t).select("id", count=CountMethod.exact).limit(1).execute()
            print(f"Total em {t}: {res.count}")
        except Exception as e:
            # logs_sessao does not have 'id', might fail
            try:
                res2 = sb.table(t).select("*", count=CountMethod.exact).limit(1).execute()
                print(f"Total em {t}: {res2.count}")
            except Exception as e2:
                print(f"Total em {t}: Erro ({e2})")
            
    print("\nVerificando duplicidades de pregão...")
    try:
        res_dup = sb.table("licitacoes").select("numero_pregao").limit(3000).execute()
        if res_dup.data:
            numeros = [r.get("numero_pregao") for r in res_dup.data if isinstance(r, dict) and r.get("numero_pregao")]
            contagem = Counter(numeros)
            duplicados = {k: v for k, v in contagem.items() if v > 1}
            if duplicados:
                print(f"Encontrados {len(duplicados)} números de pregão duplicados (exibindo os 5 maiores):")
                for k, v in list(sorted(duplicados.items(), key=lambda x: x[1], reverse=True))[:5]:
                    print(f" - {k}: {v} vezes")
            else:
                print(" Nenhuma duplicidade encontrada nos processos analisados.")
    except Exception as e:
        print(f"Erro ao checar duplicidades: {e}")

    print("\n=== 3. ANÁLISE DE LOGS RECENTES ===")
    try:
        res_logs = sb.table("logs_sessao").select("*").in_("nivel", ["ERROR", "WARNING"]).order("registrado_em", desc=True).limit(10).execute()
        if res_logs.data:
            for log in res_logs.data:
                if isinstance(log, dict):
                    msg = str(log.get('mensagem'))[:80] + ("..." if len(str(log.get('mensagem'))) > 80 else "")
                    registrado = log.get('registrado_em') or ''
                    print(f"[{str(registrado)[:19]}] {log.get('nivel')}: {msg}")
        else:
            print(" ✅ Nenhum erro ou warning recente encontrado na tabela.")
    except Exception as e:
        print(f"Erro ao acessar logs_sessao (talvez coluna diferente): {e}")

if __name__ == "__main__":
    main()

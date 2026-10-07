# testar_tudo.py
import sys
import os
import re
from datetime import datetime

print("=" * 60)
print("🚀 BATERIA DE TESTES GERAIS: ROBÔ DE LICITAÇÕES JRBC TECH")
print("=" * 60)

# 1. Teste de Configurações (.env)
print("\n[1/5] Verificando Variáveis de Ambiente (.env)...")
from dotenv import load_dotenv
load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

if supabase_url and supabase_key:
    print(f"  ✅ Supabase Configurado: {supabase_url[:35]}...")
else:
    print("  ❌ Falha: Credenciais do Supabase ausentes no .env!")

if tg_token and tg_chat_id:
    print(f"  ✅ Telegram Configurado: Chat ID {tg_chat_id}")
else:
    print("  ❌ Falha: Credenciais do Telegram ausentes no .env!")

# 2. Teste do Banco de Dados (Supabase)
print("\n[2/5] Testando Conexão e Integridade no Supabase...")
try:
    from supabase_client import get_client
    sb = get_client()
    res = sb.table("licitacoes").select("id, objeto, status_analise", count="exact").execute()
    total = res.count or len(res.data or [])
    print(f"  ✅ Conectado ao Supabase com sucesso!")
    print(f"  📊 Total de licitações ativas na base: {total}")
except Exception as e:
    print(f"  ❌ Erro ao consultar Supabase: {e}")

# 3. Teste das Regras de CNAE (Filtro JRBC TECH)
print("\n[3/5] Testando Filtro Rígido de CNAE (CCMEI)...")
try:
    from radar_telegram import validar_edital_jrbc

    # Casos que DEVEM ser aprovados
    caso_itacaja = "Contratação de empresa especializada em TI para prestação de serviços de gestão, otimização e sustentação avançada da infraestrutura tecnológica para suporte técnico de nível 3 em hardware, periféricos e ativos de rede de dados..."
    caso_misto = "Aquisição de peças e serviço para conserto de Nobreak SMS"

    # Casos que DEVEM ser reprovados
    caso_saude = "Aquisição de medicamentos e equipamentos médico-hospitalares"
    caso_tenda = "Locação de tendas, palcos e iluminação para festividade de rally"
    caso_balcao = "Aquisição de material de expediente e papelaria para prefeitura"

    assert validar_edital_jrbc(caso_itacaja) is True, "Falha: Itacajá deveria ser aprovado!"
    assert validar_edital_jrbc(caso_misto) is True, "Falha: Peças + Serviço deveria ser aprovado!"
    assert validar_edital_jrbc(caso_saude) is False, "Falha: Saúde deveria ser rejeitado!"
    assert validar_edital_jrbc(caso_tenda) is False, "Falha: Tendas deveria ser rejeitado!"
    assert validar_edital_jrbc(caso_balcao) is False, "Falha: Papelaria pura deveria ser rejeitada!"

    print("  ✅ Filtro de CNAE 100% calibrado (Aprova TI/Redes e Reprova Saúde/Tendas/Papelaria).")
except Exception as e:
    print(f"  ❌ Falha no teste de CNAE: {e}")

# 4. Teste de Notificação e Botões no Telegram
print("\n[4/5] Testando Disparo para o Telegram (@Jrbctech)...")
try:
    from telegram_service import TelegramService
    hora_atual = datetime.now().strftime("%H:%M:%S")
    msg = f"🧪 *TESTE DE SISTEMA JRBC TECH*\n\nTodos os módulos foram checados via terminal às *{hora_atual}*.\nStatus: *Operacional* ✅"
    
    sucesso = TelegramService.enviar_mensagem(msg)
    if sucesso:
        print("  ✅ Mensagem de teste entregue no seu Telegram com sucesso!")
    else:
        print("  ⚠️ Telegram retornou falha no envio.")
except Exception as e:
    print(f"  ❌ Erro ao enviar mensagem no Telegram: {e}")

# 5. Teste da Porta CDP do Chrome (:9222)
print("\n[5/5] Checando Conexão com Google Chrome (Porta 9222)...")
try:
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:9222/json/version")
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("  ✅ Google Chrome está aberto com depuração ativa na porta 9222 (Pronto para disputa!).")
except Exception:
    print("  ⚪ Chrome não está aberto na porta 9222 agora.")
    print("     (Para abrir antes da disputa: google-chrome --remote-debugging-port=9222 &)")

print("\n" + "=" * 60)
print("🏁 FIM DA BATERIA DE TESTES")
print("=" * 60)

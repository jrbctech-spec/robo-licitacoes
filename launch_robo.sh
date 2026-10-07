#!/bin/bash
set -e

# Diretório raiz do projeto
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================================="
echo "   🤖 ROBÔ DE LICITAÇÕES JRBC - INICIALIZADOR ÚNICO"
echo "=========================================================="

CHROME_URL="https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/compras"
DEBUG_PORT=9222

# 1. Verificar se a porta 9222 está ativa (usando timeout em /dev/tcp e verificação HTTP do CDP)
echo "🔍 Verificando se a porta de depuração do Chrome ($DEBUG_PORT) está ativa..."
CHROME_ATIVO=0

if curl -s --connect-timeout 1 "http://127.0.0.1:$DEBUG_PORT/json/version" > /dev/null 2>&1; then
    CHROME_ATIVO=1
elif timeout 1 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$DEBUG_PORT" 2>/dev/null; then
    CHROME_ATIVO=1
fi

if [ "$CHROME_ATIVO" -eq 1 ]; then
    echo "✅ Google Chrome já está ativo com a porta $DEBUG_PORT aberta."
else
    echo "🚀 Porta $DEBUG_PORT inativa. Iniciando Google Chrome em background..."
    google-chrome --remote-debugging-port=$DEBUG_PORT "$CHROME_URL" &
    sleep 2
    echo "✅ Google Chrome iniciado na porta $DEBUG_PORT."
fi

# 2. Ativar o ambiente virtual .venv
if [ -d ".venv" ]; then
    echo "🐍 Ativando ambiente virtual (.venv)..."
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo "🐍 Ativando ambiente virtual (venv)..."
    source venv/bin/activate
else
    echo "⚠️ Ambiente virtual não encontrado, utilizando python3 global..."
fi

# 3. Executar o Robô de Licitações (GUI)
echo "🖥️ Executando app_gui.py..."
python3 app_gui.py

#!/bin/bash
cd /home/jrbc/Documentos/robo-licitacoes

echo "🔄 Baixando atualizações do sistema..."
git pull origin main

echo "🚀 Iniciando o Robô Licitações..."
# Tenta ativar o ambiente virtual se existir
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 app_gui.py

#!/bin/bash
cd /home/jrbc/Documentos/robo-licitacoes

# Puxa atualizações do GitHub (caso você ou o GitHub Actions tenham feito commits)
git pull origin main

# Ativa o venv e roda o app
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 app_gui.py

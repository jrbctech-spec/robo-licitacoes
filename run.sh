#!/usr/bin/env bash
# ============================================================
# run.sh – Lançador do Robô de Licitações
# ============================================================
set -e

PROJECT_DIR="/home/jrbc/Documentos/robo-licitacoes"
cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

# Se tkinter estiver instalado, abre a GUI; caso contrário, abre o Menu Interativo CLI
if python -c "import tkinter" 2>/dev/null; then
    exec python main.py "$@"
else
    exec python main.py --cli "$@"
fi

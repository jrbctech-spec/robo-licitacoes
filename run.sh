#!/usr/bin/env bash
# ==============================================================================
# run.sh — Lançador Robusto do Robô de Licitações JRBC
# ==============================================================================
set -e

# Define o diretório raiz do projeto de forma absoluta
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 1. Ativação do Ambiente Virtual (.venv)
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(which python3 || which python)"
fi

if [ ! -x "$PYTHON" ]; then
    echo "❌ Erro: Interpretador Python não encontrado."
    exit 1
fi

# 2. Limpeza preventiva de travas residuais de sessões anteriores (evita travamento do Playwright/Chromium)
PERFIL_DIR="$PROJECT_DIR/browser_profile"
if [ -d "$PERFIL_DIR" ]; then
    rm -f "$PERFIL_DIR/SingletonLock" "$PERFIL_DIR/SingletonCookie" "$PERFIL_DIR/SingletonSocket" "$PERFIL_DIR/Lock" "$PERFIL_DIR/.lock" 2>/dev/null || true
fi

ALT_PERFIL="$HOME/Documentos/Robo Licitação/chromium_profile"
if [ -d "$ALT_PERFIL" ]; then
    rm -f "$ALT_PERFIL/SingletonLock" "$ALT_PERFIL/SingletonCookie" "$ALT_PERFIL/SingletonSocket" "$ALT_PERFIL/Lock" "$ALT_PERFIL/.lock" 2>/dev/null || true
fi

# 3. Execução do Robô com repasse de parâmetros
if [ "$1" = "--diagnostics" ]; then
    exec "$PYTHON" diagnostics.py
elif [ "$1" = "--cli" ]; then
    exec "$PYTHON" main.py --cli
elif [ "$1" = "--gui" ] || [ -z "$1" ]; then
    exec "$PYTHON" app_gui.py
else
    exec "$PYTHON" app_gui.py "$@"
fi


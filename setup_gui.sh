#!/usr/bin/env bash
# ============================================================
# setup_gui.sh – Instala as dependências da interface gráfica
# Execute este script UMA VEZ antes de usar o app_gui.py
#
# Uso:
#   chmod +x setup_gui.sh
#   ./setup_gui.sh
# ============================================================
set -e

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   🤖 Robô de Licitações – Setup da Interface GUI     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

PYTHON=$(which python3 || which python)
PIP_VENV=".venv/bin/pip"

# ── 1. Instalar python3-tk (requer sudo) ──────────────────────────────────────
echo "📦 Instalando python3-tk (tkinter)..."
if command -v apt-get &>/dev/null; then
    sudo apt-get install -y python3-tk python3.12-tk 2>/dev/null || \
    sudo apt-get install -y python3-tk 2>/dev/null
    echo "   ✅ python3-tk instalado via apt."
elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3-tkinter
    echo "   ✅ python3-tkinter instalado via dnf."
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm tk
    echo "   ✅ tk instalado via pacman."
else
    echo "   ⚠️  Não foi possível detectar o gerenciador de pacotes."
    echo "   Instale manualmente: sudo apt-get install python3-tk"
fi

# ── 2. Verificar tkinter ───────────────────────────────────────────────────────
echo ""
echo "🔍 Verificando tkinter..."
if $PYTHON -c "import tkinter; print('   ✅ tkinter:', tkinter.TkVersion)" 2>/dev/null; then
    echo "   tkinter disponível!"
else
    echo "   ❌ tkinter ainda não disponível."
    echo "   Verifique a instalação manual e tente novamente."
    exit 1
fi

# ── 3. Recriar venv com acesso ao tkinter do sistema ─────────────────────────
echo ""
echo "🔧 Recriando o ambiente virtual com acesso ao tkinter do sistema..."
if [ -d ".venv" ]; then
    rm -rf .venv
fi

$PYTHON -m venv .venv --system-site-packages
echo "   ✅ .venv recriado com --system-site-packages"

# ── 4. Instalar dependências Python ──────────────────────────────────────────
echo ""
echo "📥 Instalando dependências Python..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "   ✅ Dependências instaladas."

# ── 5. Instalar Chromium para Playwright ─────────────────────────────────────
echo ""
echo "🌐 Verificando Playwright / Chromium..."
if .venv/bin/playwright --version &>/dev/null; then
    .venv/bin/playwright install chromium
    echo "   ✅ Chromium do Playwright disponível."
else
    echo "   ⚠️  Playwright não encontrado no venv."
fi

# ── 6. Teste final ────────────────────────────────────────────────────────────
echo ""
echo "🧪 Teste final de importação..."
.venv/bin/python -c "
import tkinter
import customtkinter
from config import settings
from engine import MotorDecisao
from edital_parser import EditalParser
print('   ✅ Todos os módulos importam corretamente!')
print(f'   customtkinter: {customtkinter.__version__}')
print(f'   tkinter: {tkinter.TkVersion}')
"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅  Setup concluído! Para executar:                 ║"
echo "║                                                      ║"
echo "║     source .venv/bin/activate                       ║"
echo "║     python main.py                                   ║"
echo "║                                                      ║"
echo "║  Ou diretamente:  python app_gui.py                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

#!/usr/bin/env bash
# ==============================================================================
# launch_robo.sh — Lançador de compatibilidade do Robô de Licitações JRBC
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$PROJECT_DIR/run.sh" "$@"

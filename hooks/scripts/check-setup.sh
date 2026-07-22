#!/usr/bin/env bash
# SessionStart nutzt denselben Python-Resolver wie Setup und Laufzeit.
set -euo pipefail

SETUP_PY="${CLAUDE_PLUGIN_ROOT}/scripts/setup.py"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SETUP_PY" --hook-status
fi
exec python "$SETUP_PY" --hook-status

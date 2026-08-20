#!/usr/bin/env bash
# SessionStart nutzt denselben Python-Resolver wie Setup und Laufzeit.
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [[ -z "$PLUGIN_ROOT" ]]; then
  PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
SETUP_PY="${PLUGIN_ROOT}/scripts/setup.py"
[[ -f "$SETUP_PY" ]] || exit 0

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SETUP_PY" --hook-status
fi
if command -v python >/dev/null 2>&1; then
  exec python "$SETUP_PY" --hook-status
fi
printf '%s\n' "/watch: Python 3 is required. Install Python, then run the setup script."
exit 0

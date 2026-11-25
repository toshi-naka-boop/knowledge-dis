#!/usr/bin/env bash
# One-command offline trial (no Google account, no network model, nothing saved).
# Needs: Python 3.12+ and a terminal on Linux or macOS (on Windows use WSL).
# Usage: scripts/try_offline.sh            # then open the printed address
#        PORT=9000 scripts/try_offline.sh   # another port
#        DEMO_API_KEY=my-own-code scripts/try_offline.sh   # choose the access code yourself
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "Python 3.12 or newer is required (found: $("$PY" --version 2>&1 || echo none))." >&2
  echo "Install it from https://www.python.org/downloads/ and run this again." >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
echo "Installing the dependencies (first run only takes a minute or two)..."
.venv/bin/pip install -q -r scripts/requirements.txt

PORT="${PORT:-8080}"
CODE="${DEMO_API_KEY:-$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(18))')}"

echo
echo "  Open:         http://localhost:${PORT}/login"
echo "  Access code:  ${CODE}"
echo "  (Local trial only. Stop with Ctrl+C. Data is synthetic and is not saved.)"
echo

DEMO_API_KEY="${CODE}" PYTHONPATH=src:. exec .venv/bin/uvicorn \
  'knowledge_discovery.server:create_app_from_env' --factory --port "${PORT}"

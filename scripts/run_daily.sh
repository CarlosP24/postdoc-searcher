#!/bin/zsh
# Run Postdoc Radar locally (used by the launchd job, or by hand).
# Reads secrets from ../.env, writes a log to ../logs/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
.venv/bin/python -m postdoc_searcher run "$@" >> "logs/run-$(date +%Y-%m-%d).log" 2>&1

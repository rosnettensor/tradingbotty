#!/usr/bin/env bash
# Mac / Linux: ./start.sh   (add --simulate for an offline demo)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python run.py "$@"

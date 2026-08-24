#!/usr/bin/env bash
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$PROJECT_DIR/venv"
fi
"$PROJECT_DIR/venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
mkdir -p "$PROJECT_DIR/data"
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
fi
"$PROJECT_DIR/venv/bin/python" -m unittest discover -s "$PROJECT_DIR/tests"
"$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/scripts/btc_bot.py" status
printf '%s\n' "Setup complete. The bot is in PAPER mode."
printf '%s\n' "Run one scan: ./venv/bin/python scripts/btc_bot.py cycle"
printf '%s\n' "Optional Linux timer: ./scripts/install_timer.sh"

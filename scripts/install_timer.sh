#!/usr/bin/env bash
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
USER_UNITS="$HOME/.config/systemd/user"
mkdir -p "$USER_UNITS"
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/automation/btc-15m.service" \
    > "$USER_UNITS/btc-15m.service"
cp "$PROJECT_DIR/automation/btc-15m.timer" "$USER_UNITS/btc-15m.timer"
systemctl --user daemon-reload
systemctl --user enable --now btc-15m.timer
printf '%s\n' "Installed: systemctl --user status btc-15m.timer"

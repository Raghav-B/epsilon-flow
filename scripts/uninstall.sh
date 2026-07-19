#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
BACKEND_UNIT_NAME=epsilon-flow-backend.service
TRAY_UNIT_NAME=epsilon-flow-tray.service
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "$BACKEND_UNIT_NAME" "$TRAY_UNIT_NAME" >/dev/null 2>&1 || true
fi
rm -f "$UNIT_DIR/$BACKEND_UNIT_NAME" "$UNIT_DIR/$TRAY_UNIT_NAME"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

for command in epsilon-flow epsilon-flow-tray epsilon-flow-backend; do
    link="$HOME/.local/bin/$command"
    if [[ -L "$link" && $(readlink "$link") == "$INSTALL_DIR"/* ]]; then
        rm "$link"
    fi
done
rm -rf "$INSTALL_DIR"
rm -f "$HOME/.config/autostart/epsilon-flow.desktop"
printf 'Removed both Epsilon Flow runtimes and user services. Settings and transcript history were preserved.\n'

#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
for command in epsilon-flow epsilon-flow-tray epsilon-flow-backend; do
    link="$HOME/.local/bin/$command"
    if [[ -L "$link" && $(readlink "$link") == "$INSTALL_DIR"/* ]]; then
        rm "$link"
    fi
done
rm -rf "$INSTALL_DIR"
rm -f "$HOME/.config/autostart/epsilon-flow.desktop"
printf 'Removed Epsilon Flow. Settings and transcript history were preserved.\n'

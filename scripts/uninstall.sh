#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
BACKEND_UNIT_NAME=epsilon-flow-backend.service
TRAY_UNIT_NAME=epsilon-flow-tray.service
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
DESKTOP_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}/applications
ICON_THEME_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}/icons/hicolor
ICON_NAME=com.epsilon.flow

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "$BACKEND_UNIT_NAME" "$TRAY_UNIT_NAME" >/dev/null 2>&1 || true
fi
rm -f "$UNIT_DIR/$BACKEND_UNIT_NAME" "$UNIT_DIR/$TRAY_UNIT_NAME"
rm -f "$UNIT_DIR/$BACKEND_UNIT_NAME.d/cuda-libraries.conf"
rmdir "$UNIT_DIR/$BACKEND_UNIT_NAME.d" >/dev/null 2>&1 || true
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

for command in epsilon-flow epsilon-flow-tray epsilon-flow-backend; do
    link="$HOME/.local/bin/$command"
    if [[ -L "$link" && $(readlink "$link") == "$INSTALL_DIR"/* ]]; then
        rm "$link"
    fi
done
rm -f "$HOME/.local/bin/epsilon-flow-launch"
rm -rf "$INSTALL_DIR"
rm -f "$HOME/.config/autostart/epsilon-flow.desktop"
rm -f "$DESKTOP_DIR/$ICON_NAME.desktop"
rm -f "$ICON_THEME_DIR/scalable/apps/$ICON_NAME.svg"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$ICON_THEME_DIR" >/dev/null 2>&1 || true
fi
printf 'Removed both Epsilon Flow runtimes, user services, launcher, and icon. Settings and transcript history were preserved.\n'

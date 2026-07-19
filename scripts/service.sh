#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACTION=${1:-status}
PROFILE=${2:-cpu}
BACKEND_UNIT_NAME=epsilon-flow-backend.service
TRAY_UNIT_NAME=epsilon-flow-tray.service
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
BACKEND_UNIT_PATH="$UNIT_DIR/$BACKEND_UNIT_NAME"
TRAY_UNIT_PATH="$UNIT_DIR/$TRAY_UNIT_NAME"
INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
BACKEND_EXEC=${EPSILON_FLOW_BACKEND_EXEC:-"$INSTALL_DIR/.venv-backend/bin/epsilon-flow-backend"}
TRAY_EXEC=${EPSILON_FLOW_TRAY_EXEC:-"$INSTALL_DIR/.venv-desktop/bin/epsilon-flow-tray"}

install_native_service() {
    if [[ ! -x "$BACKEND_EXEC" ]]; then
        printf 'Native backend is not installed at %s\n' "$BACKEND_EXEC" >&2
        exit 1
    fi
    mkdir -p "$UNIT_DIR"
    cat >"$BACKEND_UNIT_PATH" <<EOF
[Unit]
Description=Epsilon Flow local transcription backend
After=network.target

[Service]
Type=simple
ExecStart="$BACKEND_EXEC" --host 127.0.0.1 --port 8791
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$BACKEND_UNIT_NAME"
}

install_tray_service() {
    if [[ ! -x "$TRAY_EXEC" ]]; then
        printf 'Native tray is not installed at %s\n' "$TRAY_EXEC" >&2
        exit 1
    fi
    mkdir -p "$UNIT_DIR"
    cat >"$TRAY_UNIT_PATH" <<EOF
[Unit]
Description=Epsilon Flow desktop tray
PartOf=graphical-session.target
After=graphical-session.target $BACKEND_UNIT_NAME
Wants=$BACKEND_UNIT_NAME

[Service]
Type=simple
ExecStart="$TRAY_EXEC"
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
    systemctl --user daemon-reload
}

case "$ACTION" in
    native)
        exec "$BACKEND_EXEC" --host 127.0.0.1 --port 8791
        ;;
    native-install)
        install_native_service
        install_tray_service
        ;;
    native-start)
        systemctl --user start "$BACKEND_UNIT_NAME"
        ;;
    native-stop)
        systemctl --user stop "$BACKEND_UNIT_NAME"
        ;;
    native-status)
        systemctl --user status "$BACKEND_UNIT_NAME" "$TRAY_UNIT_NAME"
        ;;
    native-logs)
        journalctl --user -u "$BACKEND_UNIT_NAME" -f
        ;;
    tray-start)
        systemctl --user start "$TRAY_UNIT_NAME"
        ;;
    tray-stop)
        systemctl --user stop "$TRAY_UNIT_NAME"
        ;;
    tray-status)
        systemctl --user status "$TRAY_UNIT_NAME"
        ;;
    tray-logs)
        journalctl --user -u "$TRAY_UNIT_NAME" -f
        ;;
    start)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" up -d "backend-$PROFILE"
        ;;
    stop)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" down
        ;;
    logs)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" logs -f "backend-$PROFILE"
        ;;
    status)
        docker compose -f "$ROOT/docker-compose.yml" ps
        ;;
    *)
        printf 'usage: %s {native|native-install|native-start|native-stop|native-status|native-logs|tray-start|tray-stop|tray-status|tray-logs|start|stop|logs|status} [cpu|cuda]\n' "$0" >&2
        exit 2
        ;;
esac

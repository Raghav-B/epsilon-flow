#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACTION=${1:-status}
PROFILE=${2:-cpu}
UNIT_NAME=epsilon-flow-backend.service
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
BACKEND_EXEC=${EPSILON_FLOW_BACKEND_EXEC:-"$INSTALL_DIR/.venv-backend/bin/epsilon-flow-backend"}

install_native_service() {
    if [[ ! -x "$BACKEND_EXEC" ]]; then
        printf 'Native backend is not installed at %s\n' "$BACKEND_EXEC" >&2
        exit 1
    fi
    mkdir -p "$UNIT_DIR"
    cat >"$UNIT_PATH" <<EOF
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
    systemctl --user enable --now "$UNIT_NAME"
}

case "$ACTION" in
    native)
        exec "$BACKEND_EXEC" --host 127.0.0.1 --port 8791
        ;;
    native-install)
        install_native_service
        ;;
    native-start)
        systemctl --user start "$UNIT_NAME"
        ;;
    native-stop)
        systemctl --user stop "$UNIT_NAME"
        ;;
    native-status)
        systemctl --user status "$UNIT_NAME"
        ;;
    native-logs)
        journalctl --user -u "$UNIT_NAME" -f
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
        printf 'usage: %s {native|native-install|native-start|native-stop|native-status|native-logs|start|stop|logs|status} [cpu|cuda]\n' "$0" >&2
        exit 2
        ;;
esac

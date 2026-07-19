#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
DESKTOP_VENV="$INSTALL_DIR/.venv-desktop"
BACKEND_VENV="$INSTALL_DIR/.venv-backend"
SYSTEM_PYTHON=${EPSILON_FLOW_SYSTEM_PYTHON:-/usr/bin/python3}

if [[ ! -x "$SYSTEM_PYTHON" ]]; then
    printf 'The distro Python is required at %s. Install python3 and python3-venv.\n' "$SYSTEM_PYTHON" >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1 && [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl is required for the native user backend service.\n' >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is required for backend readiness checks.\n' >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"

# Reinstalls replace both environments in place. Stop managed and legacy tray
# processes first so the new service cannot race an old Unix command socket.
systemctl --user stop epsilon-flow-tray.service epsilon-flow-backend.service >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "$DESKTOP_VENV/bin/python .*epsilon-flow-tray" >/dev/null 2>&1 || true

# GTK, PyGObject, and AppIndicator are ABI-coupled distro packages. The desktop
# environment must therefore inherit /usr/lib/python3/dist-packages.
"$SYSTEM_PYTHON" -m venv --clear --system-site-packages "$DESKTOP_VENV"
"$DESKTOP_VENV/bin/python" -m pip install --upgrade pip
"$DESKTOP_VENV/bin/python" -m pip install "$ROOT"

# faster-whisper has an independent Python/runtime lifecycle. Pin it to the
# project's supported backend Python regardless of the Ubuntu system version.
uv python install 3.12
uv venv --clear --python 3.12 "$BACKEND_VENV"
uv pip install --python "$BACKEND_VENV/bin/python" "$ROOT[backend]"

mkdir -p "$HOME/.local/bin"
for command in epsilon-flow epsilon-flow-tray; do
    ln -sfn "$DESKTOP_VENV/bin/$command" "$HOME/.local/bin/$command"
done
ln -sfn "$BACKEND_VENV/bin/epsilon-flow-backend" "$HOME/.local/bin/epsilon-flow-backend"

EPSILON_FLOW_BACKEND_EXEC="$BACKEND_VENV/bin/epsilon-flow-backend" \
EPSILON_FLOW_TRAY_EXEC="$DESKTOP_VENV/bin/epsilon-flow-tray" \
    "$ROOT/scripts/service.sh" native-install
"$DESKTOP_VENV/bin/epsilon-flow" apply-integrations --no-hotkey
# Start the tray for this session even when login autostart is disabled. A
# headless install leaves it ready for the next graphical login instead.
if systemctl --user is-active --quiet graphical-session.target; then
    systemctl --user start epsilon-flow-tray.service
fi

# systemd accepts a start request before Uvicorn has bound localhost. Wait for
# the actual service contract so the first dictation cannot race startup.
for _attempt in $(seq 1 20); do
    if curl --fail --silent --max-time 1 http://127.0.0.1:8791/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! curl --fail --silent --max-time 1 http://127.0.0.1:8791/health >/dev/null 2>&1; then
    printf 'The backend service did not become healthy. Inspect with: scripts/service.sh native-logs\n' >&2
    exit 1
fi

printf 'Installed Epsilon Flow in %s\n' "$INSTALL_DIR"
printf 'The native backend and desktop tray services are installed.\n'
printf 'Use scripts/service.sh native-status to inspect both services.\n'

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
if ! command -v uv >/dev/null 2>&1; then
    printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl is required for the native user backend service.\n' >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"

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
    "$ROOT/scripts/service.sh" native-install

printf 'Installed Epsilon Flow in %s\n' "$INSTALL_DIR"
printf 'The native backend is enabled and running. Start the tray with: epsilon-flow-tray\n'

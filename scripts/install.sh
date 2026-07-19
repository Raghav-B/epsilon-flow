#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
VENV="$INSTALL_DIR/.venv"

if ! command -v uv >/dev/null 2>&1; then
    printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"
uv python install 3.12
# GTK/PyGObject and AppIndicator are distribution packages; expose them to the native venv.
uv venv --python 3.12 --system-site-packages "$VENV"
uv pip install --python "$VENV/bin/python" "$ROOT[backend]"
mkdir -p "$HOME/.local/bin"
for command in epsilon-flow epsilon-flow-tray epsilon-flow-backend; do
    ln -sfn "$VENV/bin/$command" "$HOME/.local/bin/$command"
done

printf 'Installed Epsilon Flow in %s\n' "$INSTALL_DIR"
printf 'Next: epsilon-flow-backend &  then epsilon-flow-tray\n'

#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${EPSILON_FLOW_INSTALL_DIR:-"$HOME/.local/share/epsilon-flow"}
BACKEND_PYTHON="$INSTALL_DIR/.venv-backend/bin/python"
UNIT_NAME=epsilon-flow-backend.service
DROP_IN_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user/$UNIT_NAME.d
DROP_IN_PATH="$DROP_IN_DIR/cuda-libraries.conf"

if ! command -v nvidia-smi >/dev/null 2>&1; then
    printf 'nvidia-smi is required. Install a working NVIDIA driver first.\n' >&2
    exit 1
fi
if ! nvidia-smi -L >/dev/null; then
    printf 'The NVIDIA driver is installed but no usable GPU was detected.\n' >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1 && [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi
if [[ ! -x "$BACKEND_PYTHON" ]]; then
    printf 'Install Epsilon Flow first; backend Python was not found at %s\n' "$BACKEND_PYTHON" >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is required for the backend verification request.\n' >&2
    exit 1
fi

# CTranslate2 4.8 requires CUDA 12 cuBLAS and cuDNN 9. Keep these libraries
# isolated inside the backend environment rather than changing system Python.
uv pip install --python "$BACKEND_PYTHON" nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
CUDA_LIBRARY_PATH=$(
    "$BACKEND_PYTHON" - <<'PY'
import importlib.util

paths = []
for module in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):
    spec = importlib.util.find_spec(module)
    locations = list(spec.submodule_search_locations or []) if spec else []
    if not locations:
        raise SystemExit(f"Could not locate {module}")
    paths.append(locations[0])
print(":".join(paths))
PY
)

mkdir -p "$DROP_IN_DIR"
cat >"$DROP_IN_PATH" <<EOF
[Service]
Environment="LD_LIBRARY_PATH=$CUDA_LIBRARY_PATH"
EOF

systemctl --user daemon-reload
systemctl --user restart "$UNIT_NAME"
for _attempt in $(seq 1 30); do
    if curl --fail --silent --max-time 1 http://127.0.0.1:8791/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! curl --fail --silent --max-time 1 http://127.0.0.1:8791/health >/dev/null 2>&1; then
    printf 'The backend did not become healthy after CUDA configuration.\n' >&2
    exit 1
fi

# Explicit CUDA stays strict, so this request proves the model and runtime
# libraries load on the GPU instead of silently falling back to CPU.
curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    --data '{"model":"turbo","device":"cuda","compute_type":"float16"}' \
    http://127.0.0.1:8791/admin/load
printf '\nNative CUDA backend is ready.\n'

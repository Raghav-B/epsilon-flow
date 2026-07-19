# Epsilon Flow

Epsilon Flow is a private, local-first Linux dictation app. A GTK3 tray owns a reusable recording listener and local transcript snippets; a loopback FastAPI service runs faster-whisper. Nothing requires an account or cloud API.

## First install

Desktop packages vary by distribution. Install GTK3/PyGObject, Ayatana AppIndicator, FFmpeg, a clipboard tool (`wl-clipboard`, `xclip`, or `xsel`), and optionally `wtype`, `ydotool`, or `xdotool` for automatic insertion. Install [uv](https://docs.astral.sh/uv/) and run:

```bash
./scripts/install.sh
./scripts/service.sh native
# in another terminal
epsilon-flow-tray
```

The native installer uses a uv-managed Python 3.12 virtual environment. Open **Settings…** from the tray to configure the hotkey, autostart, delivery, history, microphone, model/device/compute type/language, Initial Prompt, and Recognition Hints. Then run `epsilon-flow apply-integrations` on GNOME to apply the saved desktop integrations.

Models use a Hugging Face model ID or any compatible local CTranslate2 model directory. faster-whisper downloads model IDs into its normal cache on first load; no snapshot hash is fixed by Epsilon Flow.

## Docker backend

```bash
./scripts/service.sh start cpu
# NVIDIA Container Toolkit required:
./scripts/service.sh start cuda
```

Both Compose profiles publish only `127.0.0.1:8791`. The named volume preserves downloaded model data. Override `EPSILON_FLOW_MODEL` and `EPSILON_FLOW_COMPUTE_TYPE` in the environment.

## Commands

- `epsilon-flow trigger` — start/stop through the running tray
- `epsilon-flow settings` — open settings directly
- `epsilon-flow doctor` — inspect required desktop/service dependencies
- `epsilon-flow show-settings` — print effective settings
- `epsilon-flow-backend` — run the native loopback service
- `./scripts/uninstall.sh` — remove the installed venv and launchers while preserving private settings/history

State follows XDG directories (`~/.config/epsilon-flow` and `~/.local/state/epsilon-flow`) with private permissions.

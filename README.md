# Epsilon Flow

Epsilon Flow is a public, local-first Linux dictation app. A GTK3 tray owns a reusable recording listener and local transcript snippets; a loopback FastAPI service runs faster-whisper. It needs no account or cloud API.

## Ubuntu 22.04, 24.04, and 26.04

Install the desktop/runtime packages (package names are shared by these Ubuntu releases):

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 ffmpeg wl-clipboard
# Optional notifications and automatic keyboard insertion:
sudo apt install libnotify-bin ydotool xdotool
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install Epsilon Flow:

```bash
./scripts/install.sh
epsilon-flow-tray
```

The installer creates two native environments on purpose:

- `.venv-desktop` uses `/usr/bin/python3 --system-site-packages`, because PyGObject and AppIndicator are distro packages tied to Ubuntu's GTK libraries.
- `.venv-backend` uses uv-managed Python 3.12, giving faster-whisper one consistent runtime across Ubuntu 22.04, 24.04, and 26.04.

The installer enables and starts `epsilon-flow-backend.service` as a systemd user service, so tray login autostart is paired with a backend. Manage it with:

```bash
./scripts/service.sh native-start
./scripts/service.sh native-stop
./scripts/service.sh native-status
./scripts/service.sh native-logs
```

Open **Settings…** from the tray to configure the hotkey, login autostart, delivery, history, microphone, model/device/compute type/language, Initial Prompt, and Recognition Hints. On GNOME, run `epsilon-flow apply-integrations` after saving integration changes. Pressing the hotkey again stops and finalizes an active recording.

Models use a Hugging Face model ID or a compatible local CTranslate2 directory. Model IDs download into the normal faster-whisper cache on first use.

## Docker CPU or CUDA backend

The tray remains native; Compose can replace the native transcription service. Stop the native service first because every backend binds `127.0.0.1:8791`.

```bash
./scripts/service.sh native-stop
./scripts/service.sh start cpu
./scripts/service.sh status
./scripts/service.sh stop cpu
```

For CUDA, install the NVIDIA driver and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), then verify both host and container access before starting the profile:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
./scripts/service.sh start cuda
./scripts/service.sh status
```

If `nvidia-smi` fails after enabling Secure Boot, check that the NVIDIA kernel module is signed/enrolled (or complete your distribution's MOK enrollment) rather than disabling Secure Boot as a first step.

Both profiles publish only loopback. The named volume preserves downloaded models. Override `EPSILON_FLOW_MODEL` and `EPSILON_FLOW_COMPUTE_TYPE` in the environment. Backend **Auto** mode falls back from CUDA to CPU if model loading fails or transcription exhausts CUDA memory; explicit **CUDA** remains strict and returns the failure.

## Commands

- `epsilon-flow trigger` — start/stop through the running tray
- `epsilon-flow settings` — open settings directly
- `epsilon-flow doctor` — inspect desktop and service dependencies
- `epsilon-flow show-settings` — print effective settings
- `epsilon-flow-backend` — run the native loopback service directly
- `./scripts/service.sh native-install` — recreate and enable the user service
- `./scripts/uninstall.sh` — remove both environments, launchers, autostart, and the user service while preserving settings/history

State follows XDG directories (`~/.config/epsilon-flow` and `~/.local/state/epsilon-flow`) with owner-only permissions. Uploads are bounded to 100 MiB by default; set `EPSILON_FLOW_MAX_UPLOAD_BYTES` on the backend to choose another limit.

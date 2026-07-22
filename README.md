# Epsilon Flow

Raghav's local dictation tool, originally built for working with his Epsilon agent.

Epsilon Flow is a public, local-first Linux dictation app. A GTK3 tray owns a reusable recording listener and local transcript snippets; a loopback FastAPI service runs faster-whisper. It needs no account or cloud API.

## Features

- Press-to-capture GNOME hotkey; press it again to stop and transcribe.
- Restartable systemd user services for the tray and loopback backend.
- Faster-Whisper `turbo` transcription on CPU/INT8 or NVIDIA CUDA/Float16.
- Automatic CUDA-to-CPU fallback when **Device** is set to Automatic.
- Native two-environment install or Docker Compose CPU/CUDA profiles.
- Selectable microphone, language, compute type, device, and delivery mode.
- Configurable names/terms glossary plus an optional advanced transcript-style example.
- One private transcription-service URL for the bundled backend, a LAN service, or a user-managed SSH tunnel.
- Clipboard copy, paste, virtual typing, or transcript-only delivery.
- Bounded local transcript snippets with explicit play-to-record; opening history never starts the microphone.
- Ubuntu 22.04, 24.04, and 26.04 GNOME/Wayland support.

## Screenshots

### Recording

![Epsilon Flow active recording surface](docs/images/recording.png)

### Transcript snippets

![Epsilon Flow idle transcript snippets with play-to-record](docs/images/snippets.png)

### Settings

![Epsilon Flow settings with shortcut capture and guided fields](docs/images/settings.png)

## Ubuntu 22.04, 24.04, and 26.04

Install the desktop/runtime packages (package names are shared by these Ubuntu releases):

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 curl ffmpeg wl-clipboard
# Optional notifications and automatic keyboard insertion:
sudo apt install libnotify-bin ydotool xdotool
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install Epsilon Flow:

```bash
./scripts/install.sh
```

The installer creates two native environments on purpose:

- `.venv-desktop` uses `/usr/bin/python3 --system-site-packages`, because PyGObject and AppIndicator are distro packages tied to Ubuntu's GTK libraries.
- `.venv-backend` uses uv-managed Python 3.12, giving faster-whisper one consistent runtime across Ubuntu 22.04, 24.04, and 26.04.

The installer creates systemd user services for both the loopback backend and the desktop tray. The backend is always enabled; the tray is enabled at graphical login by default and restarts if it crashes. **Start at login** in Settings controls future login startup without killing the current tray session. Manage them with:

```bash
./scripts/service.sh native-start
./scripts/service.sh native-stop
./scripts/service.sh native-status
./scripts/service.sh native-logs
./scripts/service.sh tray-status
./scripts/service.sh tray-logs
```

Open **Settings…** from the tray to configure the hotkey, login autostart, delivery, history, microphone, device/compute type/language, transcription-service URL, names and terms, and the optional advanced style example. The service row checks the configured URL when Settings opens; **Refresh** checks the URL currently typed without saving it first. **Save** applies integrations and closes Settings. Pressing the hotkey again stops and finalizes an active recording.

Leave **Style example** empty unless transcripts repeatedly use the wrong casing, punctuation, or prose style. If needed, enter a short piece of natural transcript text in the desired style. Names, acronyms, and technical vocabulary belong in **Names and terms** instead; Whisper treats the combined text as decoding context, not as guaranteed instructions or a strict dictionary.

Epsilon Flow currently uses Faster-Whisper's supported `turbo` alias for Whisper large-v3-turbo. It downloads into the normal faster-whisper cache on first use. Advanced Docker deployments can still override `EPSILON_FLOW_MODEL`, but model choice is intentionally fixed in the everyday desktop UI.

### Native CPU

The normal installer is complete for CPU use. Leave **Device** on Automatic or choose CPU; the backend selects INT8 automatically.

### Native CUDA

Current Faster-Whisper/CTranslate2 releases require an NVIDIA driver compatible with CUDA 12, plus cuBLAS for CUDA 12 and cuDNN 9. After the normal install, verify the driver and run the helper:

```bash
nvidia-smi
./scripts/install-native-cuda.sh
```

The helper installs cuBLAS/cuDNN only inside Epsilon Flow's backend environment, adds their library path to the backend's systemd user service, and performs a strict CUDA/Float16 model-load check. It does not silently fall back to CPU. If `nvidia-smi` fails with Secure Boot enabled, complete your distribution's NVIDIA MOK enrollment/signing flow first.

## Private LAN or SSH-tunnel backend

The tray sends plain HTTP requests to the single **Transcription service** URL in Settings. Epsilon Flow accepts loopback addresses, literal private LAN IPs, and IPv6 unique-local addresses; public hosts and authenticated URLs are rejected until the protocol has authentication. The service must provide Epsilon Flow's existing API:

- `GET /health`
- `POST /transcribe` using the same multipart audio/settings fields as the bundled client

To keep a backend private on another machine, create the SSH tunnel yourself and point Flow at its local end. For example, this forwards local port `8891` to backend port `8791` on the SSH host:

```bash
ssh -N \
  -L 127.0.0.1:8891:127.0.0.1:8791 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=2 \
  user@remote-host
```

Then set **Transcription service** to `http://127.0.0.1:8891`. Epsilon Flow deliberately does not create, monitor, or restart the tunnel; one configured URL is the complete routing decision.

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
- `./scripts/service.sh native-install` — recreate the backend and tray user services
- `./scripts/install-native-cuda.sh` — add and verify native CUDA 12/cuDNN 9 support
- `./scripts/service.sh tray-{start,stop,status,logs}` — manage the background tray
- `./scripts/uninstall.sh` — remove both environments, launchers, autostart, and user services while preserving settings/history

State follows XDG directories (`~/.config/epsilon-flow` and `~/.local/state/epsilon-flow`) with owner-only permissions. Uploads are bounded to 100 MiB by default; set `EPSILON_FLOW_MAX_UPLOAD_BYTES` on the backend to choose another limit.

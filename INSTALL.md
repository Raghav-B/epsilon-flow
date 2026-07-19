# Installation notes

Epsilon Flow targets Ubuntu 22.04, 24.04, and 26.04 desktops with GTK3 and PulseAudio/PipeWire compatibility.

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 ffmpeg wl-clipboard
# Optional: notifications and automatic insertion
sudo apt install libnotify-bin ydotool xdotool
```

After installing [uv](https://docs.astral.sh/uv/getting-started/installation/), run `./scripts/install.sh`. The desktop environment deliberately uses distro `/usr/bin/python3` with `--system-site-packages` so GTK/PyGObject/AppIndicator share Ubuntu's native ABI. The separate faster-whisper backend uses uv-managed Python 3.12 for consistent backend dependencies on all three Ubuntu versions.

The installer creates systemd user services for the backend and graphical tray. Use `scripts/service.sh native-{install,start,stop,status,logs}` for the backend and `scripts/service.sh tray-{start,stop,status,logs}` for the tray. The tray starts at graphical login by default; Settings can disable future login startup without killing the current session. Run `epsilon-flow doctor` after installation.

For isolation, stop the native service and use `scripts/service.sh start cpu` or `scripts/service.sh start cuda`. CUDA requires a working NVIDIA driver and NVIDIA Container Toolkit. Verify with both `nvidia-smi` and `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`. With Secure Boot enabled, enroll/sign the NVIDIA module through the distribution's MOK flow if host GPU detection fails.

The fixed public model is `deepdml/faster-whisper-large-v3-turbo-ct2`; faster-whisper downloads and caches it automatically on first use. Native and Docker backends bind only `127.0.0.1:8791`.

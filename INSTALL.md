# Installation notes

Epsilon Flow currently targets Linux desktops with GTK3 and PulseAudio/PipeWire compatibility. Install distribution packages for GTK3, PyGObject, Ayatana AppIndicator, FFmpeg, notifications, and clipboard/virtual-keyboard tools before running `scripts/install.sh`.

The Python environment is intentionally native rather than a container so GTK uses the logged-in desktop session. The transcription backend can be native (`scripts/service.sh native`) or isolated in the CPU/CUDA Compose profiles.

Run `epsilon-flow doctor` after installation. Docker CUDA additionally needs a working NVIDIA driver and NVIDIA Container Toolkit. Models are acquired by faster-whisper from the configured Hugging Face ID and cached automatically.

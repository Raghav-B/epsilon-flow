# VM GPU swap plan

## Diagnosis snapshot — 2026-07-22

The legacy Ubuntu 22 GPU VM is running and its QEMU SSH forward is open on
`127.0.0.1:2222`. The failing hop is the separate SSH tunnel from host port
`8891` to guest port `8791`.

The exact cause is SSH host identity verification:

- the host has a saved ED25519 host key for `[127.0.0.1]:2222`;
- the live guest presents a different ED25519 key;
- the legacy router invokes SSH with `StrictHostKeyChecking=accept-new`;
- `accept-new` intentionally refuses a changed known key, so it does not open
  the tunnel and reports the generic `SSH tunnel did not open` notification.

This is a security boundary, not a VM or VFIO/GPU boot failure. Do not delete
or replace the old known-host entry until the new fingerprint has been verified
from the guest console or another trusted VM-management path.

After the SSH identity was repaired, the complete legacy route was verified:
SSH authenticates, host `8891` listens, and the guest service answers through
the tunnel. The obsolete Epsilon Voice VM workload has since been removed, so
the RTX 3050 is reserved for Whisper and Flow. Flow must still verify available
VRAM and report `vm_gpu_busy` rather than silently running CPU or failing a
model load when another GPU workload is present.

The old router also has two design weaknesses that the port must not copy:

1. It records `backend=vm` before VM readiness has succeeded, leaving the
   desktop pointed at a dead route after a failed switch.
2. It treats a PID file as proof of a usable tunnel. A live stale SSH process
   without a listener on the expected port can therefore be accepted as ready.

## Intended Epsilon Flow shape

Epsilon Flow keeps its existing host backend at `127.0.0.1:8791`. VM switching
belongs in the Epsilon Flow desktop/client layer, which already owns the
recording-to-transcription request boundary. It selects one of two loopback
backend URLs for each dictation request:

- **Host backend:** `http://127.0.0.1:8791`
- **VM backend:** host tunnel `http://127.0.0.1:8891`, forwarded to the guest
  Epsilon Flow backend at guest `127.0.0.1:8791`

This deliberately does **not** port the old HTTP router that took over port
`8791`. The Flow client can dispatch directly after the VM is proven ready,
leaving the normal local service simple and available as the safe fallback.

The guest must run the same Epsilon Flow backend API/version as the host
release. The legacy guest service uses a different application and model
contract, so it should be replaced by an explicit Flow guest-prepare/update
operation rather than treated as a compatible dependency.

## Implementation slices

### 1. Add a VM backend manager

Create a small `epsilon_flow.vm_backend` module with one explicit job:
turn a requested `vm` backend into a verified loopback URL or return a
structured failure.

Its configuration is optional and private to the local machine. It contains
only the VM SSH endpoint, guest user, dedicated known-hosts file, tunnel port,
and the existing approved VM-control command. The generic package defaults to
host-only mode and does not assume this hardware exists.

The manager owns:

- `requested_backend` versus `active_backend` state;
- VM service state queried through the existing lifecycle wrapper;
- SSH authentication and host-key verification;
- one tunnel process and its stderr receipt;
- guest API/version and health checks;
- GPU admission based on free VRAM and active compute processes;
- clean tunnel shutdown without touching GPU/VFIO binding.

### 2. Make switching transactional

A VM switch moves through visible states:

`host_ready -> vm_starting -> ssh_ready -> tunnel_ready -> guest_healthy ->
model_ready -> vm_active`

Only after `model_ready` succeeds does Epsilon Flow persist `active_backend=vm`.
Any failure leaves `active_backend=host` and tells the user the exact failed
stage. The requested preference can be retained separately, but it must never
make the next dictation silently target an unavailable VM.

The manager starts SSH with `ExitOnForwardFailure=yes`, a bounded connect
attempt, keepalive settings, and captured stderr. Tunnel readiness requires all
three checks:

1. the child process is still alive;
2. the expected loopback listener is owned by that child;
3. `GET /health` (and Flow API-version metadata) succeeds through the tunnel.

A stale PID file is deleted only after its process identity is checked. A port
already owned by another process is a named `tunnel_port_in_use` failure, not a
generic timeout.

### 3. Preserve SSH identity as an explicit trust ceremony

Use a dedicated Epsilon Flow `known_hosts` file rather than mutating the
operator's broad `~/.ssh/known_hosts` during ordinary switching.

- First setup pairs the VM only after a user-confirmed fingerprint.
- A changed key blocks switching and shows both the saved and presented
  fingerprints.
- Re-pairing is an explicit action for a deliberately rebuilt guest.
- Runtime switching uses strict verification; it never uses `accept-new`.

This makes the current failure legible and avoids training the UI to accept a
potentially wrong guest identity.

### 4. Add explicit guest preparation/update

Provide a `epsilon-flow vm prepare` path for this machine profile. It runs only
when requested, not on every VM boot. It should:

- install/update the pinned Epsilon Flow backend environment in the guest;
- install the matching release artifact and CUDA libraries;
- register a guest backend service on guest loopback port `8791`;
- verify the guest API version, CUDA availability, and a strict CUDA model
  load before reporting success;
- save a release/version receipt for host-side compatibility checks.

Model acquisition or copying belongs here as an update operation, never in the
hot path that starts a VM for dictation.

### 5. Add the product surface

Expose a compact **Compute backend** control in Flow settings/tray:

- Host (default)
- VM GPU
- status line showing the current phase and active CUDA/model state
- Start VM, Stop VM, Retry, and View diagnostics actions

The normal Flow service URL remains an implementation detail. The UI should
show a useful error such as `VM identity changed — re-pair after verifying the
guest fingerprint`, not a raw localhost port.

### 6. Test the state machine before a real GPU run

Unit tests use fake VM-control, SSH, and guest health seams for:

- successful host-to-VM and VM-to-host switches;
- slow boot/SSH readiness;
- changed host key with no trust-file mutation;
- SSH authentication failure;
- local tunnel-port collision;
- stale PID/no listener;
- guest health failure or API-version mismatch;
- a busy VM GPU with insufficient free VRAM;
- CUDA model-load failure; and
- VM failure preserving the active host backend.

A local integration harness then starts a fake guest HTTP service behind a real
SSH tunnel process. The final hardware acceptance test is:

1. verify the guest identity through a trusted console path;
2. pair it;
3. prepare the Flow guest release;
4. switch to VM GPU and transcribe a short real recording;
5. confirm the result reports `vm`, CUDA, and the expected model;
6. switch back to host and repeat; and
7. stop the VM only through the explicit user action.

## Migration order and gates

1. **Repair gate for the legacy feature:** verify the live guest host-key
   fingerprint before replacing the stale record. This is intentionally a
   human confirmation step.
2. Add the manager and fake-seam tests in Epsilon Flow. No live VM changes.
3. Add the settings/tray status surface and transactional fallback behavior.
4. Build the guest prepare/update command and compatibility receipt.
5. Pair the VM after verification, run the real acceptance test, then retire
   the old router/tray integration only after Flow has handled repeated
   host/VM switches cleanly.

## Non-goals and invariants

- No automatic PCI/VFIO bind or unbind.
- No automatic replacement of a changed SSH host key.
- No VM boot caused by CPU fallback or ordinary host-mode dictation.
- No model sync/download during a switch.
- A failed VM switch must never make ordinary host dictation unavailable.

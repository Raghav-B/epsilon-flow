"""VM backend activation rules for the future GPU transcription worker.

This module is deliberately side-effect-light: callers inject subprocess and
network seams in tests, and production defaults only live at the boundary where
we need SSH, local sockets, and the guest health endpoint.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import requests


BackendName = Literal["local", "vm"]
ProgressPhase = Literal["idle", "gpu_admission", "ssh_probe", "tunnel", "guest_health", "ready", "failed", "busy"]
FailureCode = Literal[
    "gpu_query_failed",
    "gpu_busy",
    "host_key_verification_failed",
    "ssh_probe_failed",
    "tunnel_port_collision",
    "stale_tunnel_pid",
    "tunnel_not_listening",
    "guest_health_failed",
]


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class StartedProcess:
    pid: int


class ProcessOps(Protocol):
    def run(self, args: list[str], *, timeout: float) -> ProcessResult:
        ...

    def start(self, args: list[str]) -> StartedProcess:
        ...

    def pid_alive(self, pid: int) -> bool:
        ...

    def terminate(self, pid: int) -> None:
        ...


class NetworkOps(Protocol):
    def listener_accepts(self, host: str, port: int, *, timeout: float) -> bool:
        ...

    def health(self, url: str, *, timeout: float) -> tuple[int, dict[str, Any]]:
        ...


class ClockOps(Protocol):
    def sleep(self, seconds: float) -> None:
        ...


@dataclass(frozen=True)
class VmBackendConfig:
    ssh_host: str
    ssh_user: str
    known_hosts_path: Path
    state_path: Path
    ssh_port: int = 22
    local_host: str = "127.0.0.1"
    local_port: int = 8891
    guest_host: str = "127.0.0.1"
    guest_port: int = 8791
    min_free_vram_mb: int = 3000
    command_timeout_seconds: float = 5
    network_timeout_seconds: float = 1
    tunnel_ready_attempts: int = 5
    tunnel_ready_sleep_seconds: float = 0.2
    gpu_query_command: tuple[str, ...] = (
        "nvidia-smi",
        "--query-gpu=memory.free",
        "--format=csv,noheader,nounits",
    )


@dataclass(frozen=True)
class VmBackendFailure:
    code: FailureCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


@dataclass(frozen=True)
class VmBackendProgress:
    phase: ProgressPhase
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VmBackendStatus:
    ready: bool
    progress: tuple[VmBackendProgress, ...]
    failure: VmBackendFailure | None = None
    tunnel_url: str | None = None
    free_vram_mb: int | None = None


@dataclass(frozen=True)
class BackendSelection:
    requested_backend: BackendName
    active_backend: BackendName
    vm_status: VmBackendStatus


class SubprocessOps:
    def run(self, args: list[str], *, timeout: float) -> ProcessResult:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)

    def start(self, args: list[str]) -> StartedProcess:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return StartedProcess(process.pid)

    def pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def terminate(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return


class RequestsNetworkOps:
    def listener_accepts(self, host: str, port: int, *, timeout: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def health(self, url: str, *, timeout: float) -> tuple[int, dict[str, Any]]:
        response = requests.get(url, timeout=timeout)
        payload = response.json() if response.content else {}
        return response.status_code, payload


class RealClockOps:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def strict_ssh_base_args(config: VmBackendConfig) -> list[str]:
    """Return SSH options that fail closed when the host key is unknown or changed."""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={config.known_hosts_path}",
        "-o",
        f"ConnectTimeout={int(config.command_timeout_seconds)}",
        "-p",
        str(config.ssh_port),
    ]


def select_backend(
    requested_backend: BackendName,
    config: VmBackendConfig,
    *,
    process_ops: ProcessOps | None = None,
    network_ops: NetworkOps | None = None,
    clock: ClockOps | None = None,
) -> BackendSelection:
    """Resolve requested backend into the backend that is actually safe to use.

    A failed VM activation is reported in ``vm_status`` but never flips the
    active backend to VM. The caller can surface the structured failure while
    continuing on the local backend.
    """
    if requested_backend == "local":
        return BackendSelection(
            requested_backend="local",
            active_backend="local",
            vm_status=VmBackendStatus(
                ready=False,
                progress=(VmBackendProgress("idle", "Local backend requested; VM activation skipped."),),
            ),
        )

    vm_status = activate_vm_backend(
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=clock,
    )
    active_backend: BackendName = "vm" if vm_status.ready else "local"
    return BackendSelection(requested_backend="vm", active_backend=active_backend, vm_status=vm_status)


def activate_vm_backend(
    config: VmBackendConfig,
    *,
    process_ops: ProcessOps | None = None,
    network_ops: NetworkOps | None = None,
    clock: ClockOps | None = None,
) -> VmBackendStatus:
    process_ops = process_ops or SubprocessOps()
    network_ops = network_ops or RequestsNetworkOps()
    clock = clock or RealClockOps()
    progress: list[VmBackendProgress] = []

    ssh_probe = run_strict_ssh_probe(config, process_ops)
    progress.extend(ssh_probe.progress)
    if ssh_probe.failure is not None:
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=ssh_probe.failure,
        )

    gpu_status = check_gpu_admission(config, process_ops)
    progress.extend(gpu_status.progress)
    if gpu_status.failure is not None:
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=gpu_status.failure,
            free_vram_mb=gpu_status.free_vram_mb,
        )

    reusable_tunnel = read_tunnel_state(config.state_path)
    if reusable_tunnel is not None:
        reused = verify_reusable_tunnel(config, reusable_tunnel, process_ops, network_ops)
        progress.extend(reused.progress)
        if reused.ready:
            return VmBackendStatus(
                ready=True,
                progress=tuple(progress),
                tunnel_url=reused.tunnel_url,
                free_vram_mb=gpu_status.free_vram_mb,
            )
        if reused.failure is not None and reused.failure.code == "stale_tunnel_pid":
            # A dead PID is safe to discard. Recover it here rather than making
            # every later VM switch fail until somebody removes state by hand.
            config.state_path.unlink(missing_ok=True)
            progress.append(
                VmBackendProgress(
                    "tunnel",
                    "Discarded stale tunnel state; opening a fresh tunnel.",
                    {"pid": reusable_tunnel.pid},
                )
            )
        elif reused.failure is not None:
            return VmBackendStatus(
                ready=False,
                progress=tuple(progress),
                failure=reused.failure,
                free_vram_mb=gpu_status.free_vram_mb,
            )

    # If a listener is already present without our tracked tunnel, we cannot
    # prove it reaches the intended guest. Failing here prevents silently binding
    # the VM backend to another service on the same port.
    if network_ops.listener_accepts(config.local_host, config.local_port, timeout=config.network_timeout_seconds):
        progress.append(
            VmBackendProgress(
                "failed",
                "Tunnel port is already occupied by an untracked listener.",
                {"local_host": config.local_host, "local_port": config.local_port},
            )
        )
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=VmBackendFailure(
                "tunnel_port_collision",
                "Tunnel port is already in use by a process that is not the tracked VM tunnel.",
                {"local_host": config.local_host, "local_port": config.local_port},
                retryable=False,
            ),
            free_vram_mb=gpu_status.free_vram_mb,
        )

    started = start_tunnel(config, process_ops)
    progress.extend(started.progress)
    if started.failure is not None or started.pid is None:
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=started.failure,
            free_vram_mb=gpu_status.free_vram_mb,
        )

    if not wait_for_listener(config, network_ops, clock):
        process_ops.terminate(started.pid)
        progress.append(
            VmBackendProgress(
                "failed",
                "SSH tunnel process started, but the local listener never accepted connections.",
                {"pid": started.pid, "local_host": config.local_host, "local_port": config.local_port},
            )
        )
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=VmBackendFailure(
                "tunnel_not_listening",
                "SSH tunnel process started without opening the requested local port.",
                {"pid": started.pid, "local_host": config.local_host, "local_port": config.local_port},
                retryable=True,
            ),
            free_vram_mb=gpu_status.free_vram_mb,
        )

    health_status = check_guest_health(config, network_ops)
    progress.extend(health_status.progress)
    if health_status.failure is not None:
        process_ops.terminate(started.pid)
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=health_status.failure,
            free_vram_mb=gpu_status.free_vram_mb,
        )

    write_tunnel_state(config.state_path, TunnelState(pid=started.pid, local_port=config.local_port))
    progress.append(
        VmBackendProgress(
            "ready",
            "VM tunnel is listening and the guest health endpoint is healthy.",
            {"pid": started.pid, "tunnel_url": tunnel_url(config)},
        )
    )
    return VmBackendStatus(
        ready=True,
        progress=tuple(progress),
        tunnel_url=tunnel_url(config),
        free_vram_mb=gpu_status.free_vram_mb,
    )


@dataclass(frozen=True)
class GpuAdmissionStatus:
    progress: tuple[VmBackendProgress, ...]
    failure: VmBackendFailure | None = None
    free_vram_mb: int | None = None


def check_gpu_admission(config: VmBackendConfig, process_ops: ProcessOps) -> GpuAdmissionStatus:
    progress = [VmBackendProgress("gpu_admission", "Checking guest GPU free VRAM before VM activation.")]
    remote_command = " ".join(config.gpu_query_command)
    result = process_ops.run(
        strict_ssh_base_args(config) + [ssh_destination(config), remote_command],
        timeout=config.command_timeout_seconds,
    )
    if result.returncode != 0:
        failure = VmBackendFailure(
            "gpu_query_failed",
            "Could not read guest GPU memory for VM admission.",
            {"stderr": result.stderr.strip()},
            retryable=True,
        )
        return GpuAdmissionStatus(tuple(progress), failure=failure)

    free_vram_mb = parse_free_vram_mb(result.stdout)
    if free_vram_mb is None:
        failure = VmBackendFailure(
            "gpu_query_failed",
            "Guest GPU memory query did not return a parseable free-memory value.",
            {"stdout": result.stdout.strip()},
            retryable=True,
        )
        return GpuAdmissionStatus(tuple(progress), failure=failure)

    if free_vram_mb < config.min_free_vram_mb:
        progress.append(
            VmBackendProgress(
                "busy",
                "GPU does not have enough free VRAM for VM activation.",
                {"free_vram_mb": free_vram_mb, "required_vram_mb": config.min_free_vram_mb},
            )
        )
        failure = VmBackendFailure(
            "gpu_busy",
            "GPU is busy; keeping the local backend active instead of claiming VM readiness.",
            {"free_vram_mb": free_vram_mb, "required_vram_mb": config.min_free_vram_mb},
            retryable=True,
        )
        return GpuAdmissionStatus(tuple(progress), failure=failure, free_vram_mb=free_vram_mb)

    progress.append(
        VmBackendProgress(
            "gpu_admission",
            "GPU has enough free VRAM for VM activation.",
            {"free_vram_mb": free_vram_mb, "required_vram_mb": config.min_free_vram_mb},
        )
    )
    return GpuAdmissionStatus(tuple(progress), free_vram_mb=free_vram_mb)


def parse_free_vram_mb(stdout: str) -> int | None:
    values: list[int] = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            values.append(int(text.split()[0]))
        except ValueError:
            return None
    if not values:
        return None
    return max(values)


@dataclass(frozen=True)
class TunnelState:
    pid: int
    local_port: int


def read_tunnel_state(path: Path) -> TunnelState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = payload.get("pid")
    local_port = payload.get("local_port")
    if not isinstance(pid, int) or not isinstance(local_port, int):
        return None
    return TunnelState(pid=pid, local_port=local_port)


def write_tunnel_state(path: Path, state: TunnelState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {"pid": state.pid, "local_port": state.local_port, "updated_at": time.time()}
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def verify_reusable_tunnel(
    config: VmBackendConfig,
    state: TunnelState,
    process_ops: ProcessOps,
    network_ops: NetworkOps,
) -> VmBackendStatus:
    progress: list[VmBackendProgress] = [
        VmBackendProgress("tunnel", "Found tracked VM tunnel state; verifying the process and listener.", {"pid": state.pid})
    ]
    if state.local_port != config.local_port:
        return VmBackendStatus(ready=False, progress=tuple(progress))

    if not process_ops.pid_alive(state.pid):
        progress.append(
            VmBackendProgress("failed", "Tracked tunnel PID is stale.", {"pid": state.pid})
        )
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=VmBackendFailure(
                "stale_tunnel_pid",
                "Tracked VM tunnel PID no longer exists.",
                {"pid": state.pid},
                retryable=True,
            ),
        )

    if not network_ops.listener_accepts(config.local_host, config.local_port, timeout=config.network_timeout_seconds):
        progress.append(
            VmBackendProgress(
                "failed",
                "Tracked tunnel process exists, but the local port is not listening.",
                {"pid": state.pid, "local_port": config.local_port},
            )
        )
        return VmBackendStatus(
            ready=False,
            progress=tuple(progress),
            failure=VmBackendFailure(
                "tunnel_not_listening",
                "Tracked VM tunnel process exists without a listening local port.",
                {"pid": state.pid, "local_port": config.local_port},
                retryable=True,
            ),
        )

    health_status = check_guest_health(config, network_ops)
    progress.extend(health_status.progress)
    if health_status.failure is not None:
        return VmBackendStatus(ready=False, progress=tuple(progress), failure=health_status.failure)
    return VmBackendStatus(ready=True, progress=tuple(progress), tunnel_url=tunnel_url(config))


@dataclass(frozen=True)
class SshProbeStatus:
    progress: tuple[VmBackendProgress, ...]
    failure: VmBackendFailure | None = None


def run_strict_ssh_probe(config: VmBackendConfig, process_ops: ProcessOps) -> SshProbeStatus:
    args = strict_ssh_base_args(config) + [ssh_destination(config), "true"]
    progress = [
        VmBackendProgress(
            "ssh_probe",
            "Probing VM SSH with strict known-hosts verification.",
            {"known_hosts_path": str(config.known_hosts_path)},
        )
    ]
    result = process_ops.run(args, timeout=config.command_timeout_seconds)
    if result.returncode == 0:
        return SshProbeStatus(tuple(progress))

    stderr = result.stderr.strip()
    lower_error = stderr.lower()
    if "host key verification failed" in lower_error or "no matching host key" in lower_error:
        failure = VmBackendFailure(
            "host_key_verification_failed",
            "VM SSH host key did not match the explicit known-hosts file.",
            {"stderr": stderr, "known_hosts_path": str(config.known_hosts_path)},
            retryable=False,
        )
    else:
        failure = VmBackendFailure(
            "ssh_probe_failed",
            "VM SSH probe failed before the tunnel could be opened.",
            {"stderr": stderr},
            retryable=True,
        )
    progress.append(VmBackendProgress("failed", failure.message, failure.details))
    return SshProbeStatus(tuple(progress), failure=failure)


@dataclass(frozen=True)
class StartedTunnelStatus:
    progress: tuple[VmBackendProgress, ...]
    pid: int | None = None
    failure: VmBackendFailure | None = None


def start_tunnel(config: VmBackendConfig, process_ops: ProcessOps) -> StartedTunnelStatus:
    local_forward = f"{config.local_host}:{config.local_port}:{config.guest_host}:{config.guest_port}"
    args = strict_ssh_base_args(config) + [
        "-N",
        "-L",
        local_forward,
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        ssh_destination(config),
    ]
    progress = [
        VmBackendProgress(
            "tunnel",
            "Starting SSH tunnel for the VM transcription backend.",
            {"local_forward": local_forward},
        )
    ]
    try:
        started = process_ops.start(args)
    except OSError as exc:
        failure = VmBackendFailure(
            "ssh_probe_failed",
            "Failed to start the VM SSH tunnel command.",
            {"error": f"{type(exc).__name__}: {exc}"},
            retryable=True,
        )
        progress.append(VmBackendProgress("failed", failure.message, failure.details))
        return StartedTunnelStatus(tuple(progress), failure=failure)
    return StartedTunnelStatus(tuple(progress), pid=started.pid)


def wait_for_listener(config: VmBackendConfig, network_ops: NetworkOps, clock: ClockOps) -> bool:
    for attempt in range(config.tunnel_ready_attempts):
        if network_ops.listener_accepts(config.local_host, config.local_port, timeout=config.network_timeout_seconds):
            return True
        if attempt + 1 < config.tunnel_ready_attempts:
            clock.sleep(config.tunnel_ready_sleep_seconds)
    return False


def check_guest_health(config: VmBackendConfig, network_ops: NetworkOps) -> VmBackendStatus:
    progress = [VmBackendProgress("guest_health", "Checking guest backend /health through the tunnel.")]
    try:
        status_code, payload = network_ops.health(f"{tunnel_url(config)}/health", timeout=config.network_timeout_seconds)
    except (OSError, requests.RequestException, ValueError) as exc:
        failure = VmBackendFailure(
            "guest_health_failed",
            "Guest backend /health could not be reached through the tunnel.",
            {"error": f"{type(exc).__name__}: {exc}"},
            retryable=True,
        )
        progress.append(VmBackendProgress("failed", failure.message, failure.details))
        return VmBackendStatus(ready=False, progress=tuple(progress), failure=failure)

    if status_code != 200 or payload.get("ok") is not True:
        failure = VmBackendFailure(
            "guest_health_failed",
            "Guest backend /health did not report ok=true.",
            {"status_code": status_code, "payload": payload},
            retryable=True,
        )
        progress.append(VmBackendProgress("failed", failure.message, failure.details))
        return VmBackendStatus(ready=False, progress=tuple(progress), failure=failure)
    return VmBackendStatus(ready=True, progress=tuple(progress), tunnel_url=tunnel_url(config))


def tunnel_url(config: VmBackendConfig) -> str:
    return f"http://{config.local_host}:{config.local_port}"


def ssh_destination(config: VmBackendConfig) -> str:
    return f"{config.ssh_user}@{config.ssh_host}"

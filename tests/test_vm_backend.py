import json
from pathlib import Path

from epsilon_flow.vm_backend import (
    BackendSelection,
    ProcessResult,
    StartedProcess,
    VmBackendConfig,
    select_backend,
    strict_ssh_base_args,
)


class FakeProcessOps:
    def __init__(self, *, gpu_stdout="8192\n", ssh_result=None, gpu_result=None, alive_pids=None):
        self.ssh_result = ssh_result or ProcessResult(0, "", "")
        self.gpu_result = gpu_result or ProcessResult(0, gpu_stdout, "")
        self.alive_pids = set(alive_pids or [])
        self.run_calls = []
        self.start_calls = []
        self.terminated_pids = []
        self.next_pid = 4242

    def run(self, args, *, timeout):
        self.run_calls.append(args)
        if args and args[-1].startswith("nvidia-smi"):
            return self.gpu_result
        return self.ssh_result

    def start(self, args):
        self.start_calls.append(args)
        self.alive_pids.add(self.next_pid)
        return StartedProcess(self.next_pid)

    def pid_alive(self, pid):
        return pid in self.alive_pids

    def terminate(self, pid):
        self.terminated_pids.append(pid)


class FakeNetworkOps:
    def __init__(self, *, listener_results=None, health_status=200, health_payload=None):
        self.listener_results = list(listener_results or [])
        self.health_status = health_status
        self.health_payload = {"ok": True} if health_payload is None else health_payload
        self.listener_calls = []
        self.health_calls = []

    def listener_accepts(self, host, port, *, timeout):
        self.listener_calls.append((host, port))
        if self.listener_results:
            return self.listener_results.pop(0)
        return False

    def health(self, url, *, timeout):
        self.health_calls.append(url)
        return self.health_status, self.health_payload


class NoSleepClock:
    def sleep(self, seconds):
        return None


def vm_config(tmp_path: Path) -> VmBackendConfig:
    return VmBackendConfig(
        ssh_host="vm.local",
        ssh_user="epsilon",
        known_hosts_path=tmp_path / "known_hosts",
        state_path=tmp_path / "vm-tunnel.json",
        local_port=8891,
        min_free_vram_mb=6000,
        tunnel_ready_attempts=1,
    )


def test_vm_backend_success_starts_strict_tunnel_after_listener_and_health(tmp_path):
    config = vm_config(tmp_path)
    process_ops = FakeProcessOps()
    network_ops = FakeNetworkOps(listener_results=[False, True])

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection == BackendSelection(
        requested_backend="vm",
        active_backend="vm",
        vm_status=selection.vm_status,
    )
    assert selection.vm_status.ready
    assert selection.vm_status.tunnel_url == "http://127.0.0.1:8891"
    assert network_ops.health_calls == ["http://127.0.0.1:8891/health"]

    ssh_probe_args = process_ops.run_calls[0]
    gpu_query_args = process_ops.run_calls[1]
    tunnel_args = process_ops.start_calls[0]
    for args in (ssh_probe_args, gpu_query_args, tunnel_args):
        assert "StrictHostKeyChecking=yes" in args
        assert f"UserKnownHostsFile={config.known_hosts_path}" in args
        assert "StrictHostKeyChecking=accept-new" not in args
    assert gpu_query_args[-1].startswith("nvidia-smi")
    assert "ExitOnForwardFailure=yes" in tunnel_args

    saved_state = json.loads(config.state_path.read_text())
    assert saved_state["pid"] == 4242
    assert saved_state["local_port"] == 8891


def test_vm_backend_host_key_failure_is_structured_and_never_starts_tunnel(tmp_path):
    config = vm_config(tmp_path)
    process_ops = FakeProcessOps(
        ssh_result=ProcessResult(255, "", "Host key verification failed."),
    )
    network_ops = FakeNetworkOps(listener_results=[False])

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection.requested_backend == "vm"
    assert selection.active_backend == "local"
    assert not selection.vm_status.ready
    assert selection.vm_status.failure is not None
    assert selection.vm_status.failure.code == "host_key_verification_failed"
    assert not process_ops.start_calls
    assert len(process_ops.run_calls) == 1
    assert "StrictHostKeyChecking=yes" in process_ops.run_calls[0]
    assert all("accept-new" not in value for call in process_ops.run_calls for value in call)


def test_vm_backend_identifies_tunnel_port_collision_before_tunnel_start(tmp_path):
    config = vm_config(tmp_path)
    process_ops = FakeProcessOps()
    network_ops = FakeNetworkOps(listener_results=[True])

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection.active_backend == "local"
    assert selection.vm_status.failure is not None
    assert selection.vm_status.failure.code == "tunnel_port_collision"
    assert len(process_ops.run_calls) == 2
    assert not process_ops.start_calls
    assert not network_ops.health_calls


def test_vm_backend_replaces_a_dead_tunnel_pid_with_a_fresh_tunnel(tmp_path):
    config = vm_config(tmp_path)
    config.state_path.write_text(json.dumps({"pid": 12345, "local_port": 8891}))
    process_ops = FakeProcessOps(alive_pids=set())
    network_ops = FakeNetworkOps(listener_results=[False, True])

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection.active_backend == "vm"
    assert selection.vm_status.ready
    assert process_ops.start_calls
    assert json.loads(config.state_path.read_text())["pid"] == 4242


def test_vm_backend_reports_guest_gpu_busy_instead_of_vm_ready(tmp_path):
    config = vm_config(tmp_path)
    process_ops = FakeProcessOps(gpu_stdout="1024\n")
    network_ops = FakeNetworkOps(listener_results=[False, True])

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection.requested_backend == "vm"
    assert selection.active_backend == "local"
    assert not selection.vm_status.ready
    assert selection.vm_status.free_vram_mb == 1024
    assert selection.vm_status.failure is not None
    assert selection.vm_status.failure.code == "gpu_busy"
    assert selection.vm_status.failure.retryable
    assert not process_ops.start_calls
    assert not network_ops.listener_calls
    assert process_ops.run_calls[1][-1].startswith("nvidia-smi")


def test_failed_vm_activation_keeps_requested_and_active_backends_separate(tmp_path):
    config = vm_config(tmp_path)
    process_ops = FakeProcessOps()
    network_ops = FakeNetworkOps(listener_results=[False, True], health_status=503, health_payload={"ok": False})

    selection = select_backend(
        "vm",
        config,
        process_ops=process_ops,
        network_ops=network_ops,
        clock=NoSleepClock(),
    )

    assert selection.requested_backend == "vm"
    assert selection.active_backend == "local"
    assert not selection.vm_status.ready
    assert selection.vm_status.failure is not None
    assert selection.vm_status.failure.code == "guest_health_failed"
    assert process_ops.terminated_pids == [4242]
    assert not config.state_path.exists()


def test_strict_ssh_args_use_explicit_known_hosts_without_accept_new(tmp_path):
    config = vm_config(tmp_path)

    args = strict_ssh_base_args(config)

    assert "StrictHostKeyChecking=yes" in args
    assert f"UserKnownHostsFile={config.known_hosts_path}" in args
    assert all("accept-new" not in value for value in args)

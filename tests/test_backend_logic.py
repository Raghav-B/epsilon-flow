from epsilon_flow.backend_logic import is_cuda_oom, is_loopback_host, resolve_model_config


def test_auto_device_uses_cpu_defaults_without_cuda():
    config = resolve_model_config("small.en", "auto", "default", cuda_available=False)
    assert config.device == "cpu"
    assert config.compute_type == "int8"


def test_auto_device_uses_float16_with_cuda():
    config = resolve_model_config("small.en", "auto", "default", cuda_available=True)
    assert config.device == "cuda"
    assert config.compute_type == "float16"


def test_cuda_oom_detection_is_specific_to_cuda_allocation_failures():
    assert is_cuda_oom(RuntimeError("CUDA out of memory while allocating tensor"))
    assert is_cuda_oom(RuntimeError("CUBLAS_STATUS_ALLOC_FAILED on CUDA"))
    assert not is_cuda_oom(RuntimeError("host out of memory"))
    assert not is_cuda_oom(RuntimeError("CUDA driver unavailable"))


def test_only_loopback_hosts_are_allowed():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")

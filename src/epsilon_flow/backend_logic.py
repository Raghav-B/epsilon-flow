"""Pure backend configuration rules shared by the API and tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    model: str
    device: str
    compute_type: str


def resolve_model_config(model: str, device: str, compute_type: str, cuda_available: bool) -> ModelConfig:
    actual_device = device
    if device == "auto":
        actual_device = "cuda" if cuda_available else "cpu"
    actual_compute = compute_type
    if compute_type in {"", "default"}:
        actual_compute = "float16" if actual_device == "cuda" else "int8"
    return ModelConfig(model=model, device=actual_device, compute_type=actual_compute)


def is_cuda_oom(error: BaseException) -> bool:
    message = str(error).lower()
    return "cuda" in message and any(
        phrase in message
        for phrase in ("out of memory", "memory allocation", "failed to allocate", "cublas_status_alloc_failed")
    )


def is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}

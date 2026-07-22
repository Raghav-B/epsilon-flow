from dataclasses import replace

import pytest

from epsilon_flow.settings import (
    FIXED_MODEL,
    LEGACY_VM_TUNNEL_URL,
    LOCAL_SERVICE_URL,
    AppSettings,
    SettingsStore,
)


def test_settings_round_trip_and_private_permissions(tmp_path):
    store = SettingsStore(tmp_path)
    expected = replace(
        AppSettings(),
        delivery_mode="paste",
        history_limit=12,
        recognition_hints="Epsilon",
        service_url="http://192.168.1.20:8791",
    )
    store.save(expected)

    assert store.load() == expected
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_desktop_defaults_to_login_tray_fixed_model_and_local_service():
    settings = AppSettings()

    assert settings.start_at_login is True
    assert settings.model == FIXED_MODEL
    assert settings.service_url == "http://127.0.0.1:8791"


def test_settings_migrates_stale_hidden_model(tmp_path):
    store = SettingsStore(tmp_path)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"model": "Systran/faster-whisper-large-v3-turbo"}')

    assert store.load().model == FIXED_MODEL


@pytest.mark.parametrize(
    "service_url",
    (
        "http://localhost:8791",
        "http://127.0.0.1:8891",
        "http://192.168.1.40:8791",
        "https://10.20.30.40:443",
        "http://[::1]:8791",
        "http://[fd00::2]:8791",
    ),
)
def test_settings_allow_local_private_lan_and_tunnel_endpoints(service_url):
    AppSettings(service_url=service_url).validate()


@pytest.mark.parametrize(
    "service_url",
    (
        "http://203.0.113.2:8791",
        "https://example.com:8791",
        "ftp://127.0.0.1:8791",
        "http://user:pass@127.0.0.1:8791",
        "http://127.0.0.1:8791/api",
    ),
)
def test_settings_reject_public_or_unsupported_service_endpoints(service_url):
    with pytest.raises(ValueError, match="service URL"):
        AppSettings(service_url=service_url).validate()


def test_prompt_combines_optional_style_example_and_names():
    settings = AppSettings(initial_prompt="Okay, let us inspect this.", recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Okay, let us inspect this.\n\nEpsilon, CTranslate2"


def test_prompt_labels_names_when_used_without_style_example():
    settings = AppSettings(recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Epsilon, CTranslate2"


def test_settings_migrates_private_vm_selection_to_the_existing_tunnel(tmp_path):
    store = SettingsStore(tmp_path)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        '{"service_url": "http://127.0.0.1:8794", "compute_backend": "vm"}'
    )

    assert store.load().service_url == LEGACY_VM_TUNNEL_URL


def test_settings_migrates_old_local_fallback_to_public_default(tmp_path):
    store = SettingsStore(tmp_path)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"service_url": "http://127.0.0.1:8794"}')

    assert store.load().service_url == LOCAL_SERVICE_URL

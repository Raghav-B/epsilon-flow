from dataclasses import replace
import getpass

import pytest

from epsilon_flow.settings import FIXED_MODEL, LOCAL_SERVICE_URL, AppSettings, SettingsStore, vm_backend_config


def test_settings_round_trip_and_private_permissions(tmp_path):
    store = SettingsStore(tmp_path)
    expected = replace(
        AppSettings(),
        delivery_mode="paste",
        history_limit=12,
        recognition_hints="Epsilon",
        compute_backend="vm",
    )
    store.save(expected)

    assert store.load() == expected
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_desktop_defaults_to_login_tray_and_fixed_public_model():
    settings = AppSettings()

    assert settings.start_at_login is True
    assert settings.model == FIXED_MODEL


def test_settings_migrates_stale_hidden_model(tmp_path):
    store = SettingsStore(tmp_path)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"model": "Systran/faster-whisper-large-v3-turbo"}')

    assert store.load().model == FIXED_MODEL


def test_settings_reject_remote_service():
    with pytest.raises(ValueError, match="localhost"):
        AppSettings(service_url="http://203.0.113.2:8791").validate()


def test_settings_reject_unknown_compute_backend():
    with pytest.raises(ValueError, match="compute backend"):
        AppSettings(compute_backend="router").validate()


def test_vm_backend_config_uses_current_host_facts(tmp_path):
    state = tmp_path / "state"
    config = vm_backend_config(config_dir=tmp_path / "config", state_directory=state)

    assert config.ssh_host == "127.0.0.1"
    assert config.ssh_user == getpass.getuser()
    assert config.ssh_port == 2222
    assert config.local_host == "127.0.0.1"
    assert config.local_port == 8891
    assert config.guest_host == "127.0.0.1"
    assert config.guest_port == 8791
    assert config.state_path == state / "vm-tunnel.json"


def test_prompt_combines_initial_prompt_and_hints():
    settings = AppSettings(initial_prompt="Use names.", recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Use names.\n\nRecognition hints: Epsilon, CTranslate2"


def test_prompt_labels_recognition_hints_when_used_alone():
    settings = AppSettings(recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Recognition hints: Epsilon, CTranslate2"


def test_settings_migrates_legacy_router_url_to_direct_local_service(tmp_path):
    store = SettingsStore(tmp_path)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"service_url": "http://127.0.0.1:8791"}')

    assert store.load().service_url == LOCAL_SERVICE_URL

from dataclasses import replace

import pytest

from epsilon_flow.settings import FIXED_MODEL, AppSettings, SettingsStore


def test_settings_round_trip_and_private_permissions(tmp_path):
    store = SettingsStore(tmp_path)
    expected = replace(AppSettings(), delivery_mode="paste", history_limit=12, recognition_hints="Epsilon")
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


def test_prompt_combines_initial_prompt_and_hints():
    settings = AppSettings(initial_prompt="Use names.", recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Use names.\n\nRecognition hints: Epsilon, CTranslate2"


def test_prompt_labels_recognition_hints_when_used_alone():
    settings = AppSettings(recognition_hints="Epsilon, CTranslate2")
    assert settings.prompt() == "Recognition hints: Epsilon, CTranslate2"

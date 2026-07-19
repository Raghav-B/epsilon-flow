from dataclasses import replace

import pytest

from epsilon_flow.settings import AppSettings, SettingsStore


def test_settings_round_trip_and_private_permissions(tmp_path):
    store = SettingsStore(tmp_path)
    expected = replace(AppSettings(), delivery_mode="paste", history_limit=12, recognition_hints="Epsilon")
    store.save(expected)

    assert store.load() == expected
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_settings_reject_remote_service():
    with pytest.raises(ValueError, match="localhost"):
        AppSettings(service_url="http://192.168.1.2:8791").validate()


def test_prompt_combines_initial_prompt_and_hints():
    settings = AppSettings(initial_prompt="Use names.", recognition_hints="Epsilon, Floramis")
    assert settings.prompt() == "Use names.\n\nRecognition hints: Epsilon, Floramis"


def test_prompt_labels_recognition_hints_when_used_alone():
    settings = AppSettings(recognition_hints="Epsilon, Floramis")
    assert settings.prompt() == "Recognition hints: Epsilon, Floramis"

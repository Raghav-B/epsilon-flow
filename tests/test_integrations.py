from epsilon_flow import integrations


def test_systemd_autostart_enables_running_tray_service(tmp_path, monkeypatch):
    unit_path = tmp_path / "systemd" / "user" / integrations.TRAY_UNIT_NAME
    unit_path.parent.mkdir(parents=True)
    unit_path.touch()
    legacy_path = tmp_path / "autostart" / "epsilon-flow.desktop"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.touch()
    commands = []

    monkeypatch.setattr(
        integrations,
        "user_config_path",
        lambda name, appauthor=False: tmp_path / name,
    )
    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(integrations.subprocess, "run", lambda command, check: commands.append((command, check)))

    assert integrations.set_autostart(True) == unit_path
    assert commands == [(["systemctl", "--user", "enable", "--now", integrations.TRAY_UNIT_NAME], True)]
    assert not legacy_path.exists()


def test_systemd_autostart_disable_keeps_current_tray_running(tmp_path, monkeypatch):
    unit_path = tmp_path / "systemd" / "user" / integrations.TRAY_UNIT_NAME
    unit_path.parent.mkdir(parents=True)
    unit_path.touch()
    commands = []

    monkeypatch.setattr(
        integrations,
        "user_config_path",
        lambda name, appauthor=False: tmp_path / name,
    )
    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(integrations.subprocess, "run", lambda command, check: commands.append((command, check)))

    assert integrations.set_autostart(False) == unit_path
    assert commands == [(["systemctl", "--user", "disable", integrations.TRAY_UNIT_NAME], True)]

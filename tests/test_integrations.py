from types import SimpleNamespace

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


def test_gnome_hotkey_binding_can_be_temporarily_suspended(monkeypatch):
    commands = []

    monkeypatch.setattr(
        integrations.shutil,
        "which",
        lambda name: "/usr/bin/gsettings" if name == "gsettings" else None,
    )
    monkeypatch.setattr(
        integrations.subprocess,
        "run",
        lambda command, check: commands.append((command, check)),
    )

    integrations.set_gnome_hotkey_binding("")
    integrations.set_gnome_hotkey_binding("<Ctrl><Shift>F9")

    schema_path = f"{integrations.SCHEMA}:{integrations.HOTKEY_PATH}"
    assert commands == [
        (["gsettings", "set", schema_path, "binding", ""], True),
        (["gsettings", "set", schema_path, "binding", "<Ctrl><Shift>F9"], True),
    ]


def test_bind_gnome_hotkey_registers_flow_and_its_accelerator(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout="@as []\n")

    monkeypatch.setattr(
        integrations.shutil,
        "which",
        lambda name: "/usr/bin/gsettings" if name == "gsettings" else "/usr/bin/epsilon-flow",
    )
    monkeypatch.setattr(integrations.subprocess, "run", fake_run)

    integrations.bind_gnome_hotkey("<Ctrl><Shift>F9")

    schema_path = f"{integrations.SCHEMA}:{integrations.HOTKEY_PATH}"
    assert commands[-1] == (
        ["gsettings", "set", schema_path, "binding", "<Ctrl><Shift>F9"],
        {"check": True},
    )
    assert ["gsettings", "set", schema_path, "command", "/usr/bin/epsilon-flow trigger"] in [
        command for command, _kwargs in commands
    ]


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

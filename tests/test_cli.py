from epsilon_flow.cli import set_service_url
from epsilon_flow.settings import SettingsStore


def test_set_service_url_persists_validated_endpoint(tmp_path, capsys):
    store = SettingsStore(tmp_path)

    assert set_service_url("http://127.0.0.1:8891/", store) == 0

    assert store.load().service_url == "http://127.0.0.1:8891"
    assert capsys.readouterr().out.strip() == "http://127.0.0.1:8891"

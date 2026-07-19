from epsilon_flow.history import TranscriptHistory


def test_history_is_newest_first_and_bounded(tmp_path):
    history = TranscriptHistory(tmp_path, limit=2)
    first = history.add("one")
    history.add("two")
    history.add("three")

    assert [entry["text"] for entry in history.load()] == ["three", "two"]
    assert history.update(first["id"], delivery_status="copied") is None
    assert history.path.stat().st_mode & 0o777 == 0o600


def test_history_update_and_clear(tmp_path):
    history = TranscriptHistory(tmp_path)
    entry = history.add("recover me")
    updated = history.update(entry["id"], delivery_status="paste_sent")

    assert updated["delivery_status"] == "paste_sent"
    history.clear()
    assert history.load() == []

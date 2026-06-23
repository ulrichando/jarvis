"""Phase 1, Task 3: learning a new fact (memory add/replace) wakes the
cognitive evolution loop; read/remove do not."""
from pipeline.automod import experience_signal as signal
from tools import memory


def test_memory_add_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    memory._signal_new_fact("add")
    assert bumped and bumped[0].startswith("fact:")


def test_memory_replace_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    memory._signal_new_fact("replace")
    assert bumped and bumped[0].startswith("fact:")


def test_memory_read_does_not_bump(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    memory._signal_new_fact("read")
    assert bumped == []

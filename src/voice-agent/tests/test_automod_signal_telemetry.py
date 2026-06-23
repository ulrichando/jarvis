"""Phase 1, Task 2: a turn carrying a correction or bad confab state wakes
the cognitive evolution loop; a clean turn does not."""
from pipeline.automod import signal
from pipeline import turn_telemetry


def test_correction_turn_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal="stop saying sir", confab_check_state=None
    )
    assert bumped and bumped[0].startswith("correction:")


def test_confab_turn_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal=None, confab_check_state="hedged_no_evidence"
    )
    assert bumped and bumped[0].startswith("confab:")


def test_clean_turn_does_not_bump(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal=None, confab_check_state="ok"
    )
    assert bumped == []

"""Cross-process AEC state file (voice-client writer → agent reader)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_write_then_read_roundtrip(tmp_path):
    from audio.aec_state import write_aec_state, read_aec_state
    p = tmp_path / "aec-state.json"
    write_aec_state(p, output_profile="speakers", l1_active=True,
                    l2_aec_active=False, l3_active=True,
                    apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1)
    state = read_aec_state(p, max_age_s=60)
    assert state["output_profile"] == "speakers"
    assert state["aec_layer1_active"] == 1
    assert state["aec_layer2_aec_active"] == 0
    assert state["aec_layer3_active"] == 1
    assert state["apm_delay_ms_p50"] == 42
    assert state["dtln_latency_ms_p95"] == 3.1


def test_stale_file_returns_nulls(tmp_path):
    from audio.aec_state import write_aec_state, read_aec_state
    p = tmp_path / "aec-state.json"
    write_aec_state(p, output_profile="speakers", l1_active=True,
                    l2_aec_active=False, l3_active=True,
                    apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1)
    # Force the updated_utc to look 120s old.
    import json
    data = json.loads(p.read_text())
    data["updated_utc"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(data))
    state = read_aec_state(p, max_age_s=60)
    assert state["output_profile"] is None
    assert state["aec_layer1_active"] is None


def test_missing_file_returns_nulls(tmp_path):
    from audio.aec_state import read_aec_state
    state = read_aec_state(tmp_path / "nope.json", max_age_s=60)
    assert all(v is None for v in state.values())

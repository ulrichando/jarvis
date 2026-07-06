"""Regression guard for the 2026-06-21 OOM crash loop.

When local STT was promoted to PRIMARY, each LiveKit job process began
loading the ~1.6 GB faster-whisper large-v3-turbo model in-process. The
per-job cap was 1500 MB (sized for the cloud-STT footprint), so the
framework killed every job mid-transcription (exit -10) and respawned —
JARVIS went silent because no turn ever completed. ``job_memory_limit_mb()``
raises the default to 5000 whenever in-process local STT is enabled.
"""
from pipeline import config


def test_default_cloud_stt(monkeypatch):
    """Cloud-STT machines (no in-process model) keep the protective 1500."""
    monkeypatch.delenv("JARVIS_JOB_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    assert config.job_memory_limit_mb() == 1500.0


def test_local_stt_raises_default(monkeypatch):
    """In-process local STT must clear base job (~635 MB) + ~1.6 GB model."""
    monkeypatch.delenv("JARVIS_JOB_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    assert config.job_memory_limit_mb() >= 4000.0


def test_explicit_override_wins(monkeypatch):
    """An operator override beats the local-STT default in either direction."""
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_JOB_MEMORY_LIMIT_MB", "2000")
    assert config.job_memory_limit_mb() == 2000.0


def test_zero_override_preserved(monkeypatch):
    """0 is the framework's 'disable the cap' sentinel — pass it through."""
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_JOB_MEMORY_LIMIT_MB", "0")
    assert config.job_memory_limit_mb() == 0.0


def test_invalid_override_falls_back(monkeypatch):
    """A garbage override falls back to the route-appropriate default."""
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    monkeypatch.setenv("JARVIS_JOB_MEMORY_LIMIT_MB", "not-a-number")
    assert config.job_memory_limit_mb() == 1500.0


# ── job_memory_warn_mb() — the warn companion (2026-07-06) ────────────
# The framework's 500 MB warn default sat far under the ~1.3 GB local-STT
# steady state, so "process memory usage is high" fired every ~5 s and was
# 40% of live log volume. The warn threshold now rises with the cap.


def test_warn_default_cloud_stt(monkeypatch):
    monkeypatch.delenv("JARVIS_JOB_MEMORY_WARN_MB", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    assert config.job_memory_warn_mb() == 500.0


def test_warn_local_stt_clears_steady_state(monkeypatch):
    """Must clear the observed 990-1540 MB healthy band, stay under the cap."""
    monkeypatch.delenv("JARVIS_JOB_MEMORY_WARN_MB", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    warn = config.job_memory_warn_mb()
    assert warn > 1540.0
    assert warn < config.job_memory_limit_mb()


def test_warn_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_JOB_MEMORY_WARN_MB", "800")
    assert config.job_memory_warn_mb() == 800.0

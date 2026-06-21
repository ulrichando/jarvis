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

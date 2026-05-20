"""Integration — mic path layer gating + barge-in (no mic-drop on speakers)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_should_publish_during_speak_logic():
    """2026-05-20 — mic gate now uses measured EchoDefense + deny-by-default
    on speakers (_HOT_MIC_SET='none' until a soak promotes it).
    headphones always pass (no echo path). Spec §4.2."""
    import jarvis_voice_client as vc
    from audio.aec_health import EchoDefense
    decide = vc._should_publish_during_speak
    # headphones → always publish regardless of AEC state:
    assert decide(profile="headphones", defense=EchoDefense(l1=False, l2_aec=False, l3=False)) is True
    assert decide(profile="headphones", defense=EchoDefense(l1=True, l2_aec=True, l3=True)) is True
    # speakers → deny-by-default (_HOT_MIC_SET='none' — no soak validation yet):
    assert decide(profile="speakers", defense=EchoDefense(l1=False, l2_aec=False, l3=False)) is False
    assert decide(profile="speakers", defense=EchoDefense(l1=True, l2_aec=False, l3=False)) is False
    assert decide(profile="speakers", defense=EchoDefense(l1=True, l2_aec=True, l3=True)) is False
    # unknown profile (no headphones detected) → also deny-by-default:
    assert decide(profile="unknown", defense=EchoDefense(l1=True, l2_aec=True, l3=True)) is False

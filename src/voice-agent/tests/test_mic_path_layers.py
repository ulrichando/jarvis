"""Integration — mic path layer gating + barge-in (no mic-drop on speakers)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_should_publish_during_speak_logic():
    """Mic gate uses measured EchoDefense with profile-aware policy.
    Current _HOT_MIC_SET='l1_l3' (promoted from 'none' on 2026-05-23
    via commit 7fc3ed6d once DTLN L3 shipped): speakers pass only when
    both L1 (platform echo-cancel) AND L3 (DTLN residual suppression)
    are measured active. Headphones always pass (no echo path)."""
    import jarvis_voice_client as vc
    from audio.aec_health import EchoDefense
    decide = vc._should_publish_during_speak
    # headphones → always publish regardless of AEC state:
    assert decide(profile="headphones", defense=EchoDefense(l1=False, l2_aec=False, l3=False)) is True
    assert decide(profile="headphones", defense=EchoDefense(l1=True, l2_aec=True, l3=True)) is True
    # speakers → require L1 AND L3 (l2_aec is informational, not gating):
    assert decide(profile="speakers", defense=EchoDefense(l1=False, l2_aec=False, l3=False)) is False
    assert decide(profile="speakers", defense=EchoDefense(l1=True,  l2_aec=False, l3=False)) is False
    assert decide(profile="speakers", defense=EchoDefense(l1=False, l2_aec=True,  l3=True))  is False
    assert decide(profile="speakers", defense=EchoDefense(l1=True,  l2_aec=False, l3=True))  is True
    assert decide(profile="speakers", defense=EchoDefense(l1=True,  l2_aec=True,  l3=True))  is True
    # unknown profile → treated same as speakers (no headphones exception);
    # passes only when L1+L3 are measured active.
    assert decide(profile="unknown", defense=EchoDefense(l1=False, l2_aec=False, l3=False)) is False
    assert decide(profile="unknown", defense=EchoDefense(l1=True,  l2_aec=True,  l3=True))  is True

"""Integration — mic path layer gating + barge-in (no mic-drop on speakers)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_should_publish_during_speak_logic():
    """The core barge-in fix: decide whether to publish mic frames
    while the agent is speaking, based on profile + active AEC."""
    import jarvis_voice_client as vc
    decide = vc._should_publish_during_speak
    # speakers + some AEC active → publish (barge-in works):
    assert decide(profile="speakers", apm_aec=False, neural_aec=True) is True
    assert decide(profile="speakers", apm_aec=True, neural_aec=False) is True
    # headphones → always publish (no echo path):
    assert decide(profile="headphones", apm_aec=False, neural_aec=False) is True
    # speakers + NO AEC at all → don't publish (legacy mic-drop safety net):
    assert decide(profile="speakers", apm_aec=False, neural_aec=False) is False

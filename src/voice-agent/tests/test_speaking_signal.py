import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import numpy as np
from audio.speaking_signal import is_rendering_speech

def test_silence_is_not_speech():
    assert is_rendering_speech(np.zeros(480, dtype=np.int16)) is False

def test_loud_pcm_is_speech():
    assert is_rendering_speech(np.ones(480, dtype=np.int16) * 8000) is True

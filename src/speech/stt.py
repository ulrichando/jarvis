"""Speech-to-Text — Whisper-based transcription for JARVIS.

Primary: Groq Whisper API (free, fast, same model)
Fallback: Local faster-whisper (CTranslate2) when offline/API fails
"""

import subprocess
import io
import os
import numpy as np
from src.config import STT_MODEL

# Lazy imports — these are heavy
_whisper_model = None
_groq_api_key = None


def _get_groq_key() -> str:
    """Get Groq API key from providers.json."""
    global _groq_api_key
    if _groq_api_key is not None:
        return _groq_api_key
    try:
        import json
        from src.config import JARVIS_HOME
        with open(JARVIS_HOME / "providers.json") as f:
            data = json.load(f)
        _groq_api_key = data.get("groq", {}).get("api_key", "")
    except Exception:
        _groq_api_key = os.environ.get("GROQ_API_KEY", "")
    return _groq_api_key


def _transcribe_groq(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe audio via Groq Whisper API. Returns empty string on failure."""
    key = _get_groq_key()
    if not key:
        return ""

    try:
        import wave
        import requests

        # Convert numpy float32 to WAV bytes in memory
        buf = io.BytesIO()
        int16_audio = (audio * 32767).astype(np.int16)
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int16_audio.tobytes())
        buf.seek(0)

        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("audio.wav", buf, "audio/wav")},
            data={"model": "whisper-large-v3-turbo", "language": "en"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except Exception as e:
        print(f"[JARVIS] Groq Whisper failed: {e}")
        return ""


_fine_tuned_model = None
_fine_tuned_processor = None


def _get_fine_tuned_model():
    """Load LoRA fine-tuned Whisper if available."""
    global _fine_tuned_model, _fine_tuned_processor
    if _fine_tuned_model is not None:
        return _fine_tuned_model, _fine_tuned_processor

    from src.config import JARVIS_HOME
    lora_path = JARVIS_HOME / "models" / "whisper-jarvis-lora"
    if not lora_path.exists():
        return None, None

    try:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        from peft import PeftModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32

        base = WhisperForConditionalGeneration.from_pretrained(
            "openai/whisper-large-v3-turbo", torch_dtype=dtype,
        )
        _fine_tuned_model = PeftModel.from_pretrained(base, str(lora_path)).to(device)
        _fine_tuned_processor = WhisperProcessor.from_pretrained(str(lora_path))
        print("[JARVIS] Fine-tuned Whisper LoRA loaded")
        return _fine_tuned_model, _fine_tuned_processor
    except Exception as e:
        print(f"[JARVIS] Fine-tuned model load failed: {e}")
        return None, None


def _transcribe_fine_tuned(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe using the LoRA fine-tuned model."""
    model, processor = _get_fine_tuned_model()
    if model is None:
        return ""

    try:
        import torch
        device = next(model.parameters()).device
        inputs = processor(audio, sampling_rate=sample_rate, return_tensors="pt").to(device)
        with torch.no_grad():
            ids = model.generate(**inputs, max_new_tokens=128, language="en")
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        return text
    except Exception as e:
        print(f"[JARVIS] Fine-tuned transcription failed: {e}")
        return ""


# Custom vocabulary — words JARVIS should always recognize correctly
CUSTOM_VOCAB = {
    "jarvis", "ulrich", "berbon", "cogscript", "neural lattice",
    "ollama", "groq", "anthropic", "whisper", "haiku", "sonnet", "opus",
    "kali", "linux", "webkit", "websocket",
}


def _boost_vocabulary(text: str) -> str:
    """Post-process transcription to fix common misrecognitions of custom words."""
    if not text:
        return text
    # Case-insensitive replacements for known words
    _FIXES = {
        "jarves": "jarvis", "jarves'": "jarvis", "jarvus": "jarvis",
        "jervis": "jarvis", "javis": "jarvis", "jarbus": "jarvis",
        "ulrick": "ulrich", "ulrik": "ulrich",
        "burbonne": "berbon", "bourbon": "berbon", "burbon": "berbon",
        "cog script": "cogscript", "kog script": "cogscript",
        "allah ma": "ollama", "olama": "ollama", "o llama": "ollama",
        "grok": "groq", "groak": "groq",
        "haycoo": "haiku", "highku": "haiku",
        "kali linux": "kali linux",
    }
    lower = text.lower()
    for wrong, right in _FIXES.items():
        if wrong in lower:
            # Preserve original casing style
            idx = lower.find(wrong)
            text = text[:idx] + right + text[idx + len(wrong):]
            lower = text.lower()
    return text


_LOCAL_MODEL_PATH = os.path.expanduser(
    "~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
    "/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
)


def _get_model():
    """Lazy-load the Whisper model. Auto-selects device based on hardware.

    Uses the pinned local cache path so it works fully air-gapped — no
    network request is made even if Groq is unreachable.
    """
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        # Auto-detect best device
        device = "cpu"
        compute = "int8"
        try:
            from src.hardware import detect_hardware
            hw = detect_hardware()
            if hw.has_cuda:
                device = "cuda"
                compute = "float16"
        except Exception:
            pass

        # Prefer pinned local path (air-gapped safe); fall back to name-based
        # resolution only if the cache directory is missing.
        model_id = _LOCAL_MODEL_PATH if os.path.isdir(_LOCAL_MODEL_PATH) else "large-v3-turbo"
        _whisper_model = WhisperModel(
            model_id,
            device=device,
            compute_type=compute,
            num_workers=2,
        )
    return _whisper_model


# Common Whisper hallucinations on silent/quiet audio
_HALLUCINATIONS = {
    "thank you for watching", "thanks for watching", "thank you for listening",
    "thanks for listening", "subscribe", "like and subscribe",
    "please subscribe", "see you next time", "bye", "goodbye",
    "thank you", "thanks", "you", "the end", "so", "okay",
    "um", "uh", "hmm", "ah", "oh", "i'm sorry",
    "subtitles by", "translated by", "amara.org",
    "transcribed by", "copyright", "all rights reserved",
    "music", "applause", "laughter", "silence",
    "...", "…", "♪", "♫",
    # Common noise hallucinations
    "mm-hmm", "mm", "mmm", "hmm", "huh", "shh",
    "no", "yes", "yeah", "yep", "nope", "right",
    "i see", "sure", "one", "two", "and",
    "i dare", "soon", "one second", "just",
    "go", "stop", "wait", "come", "what", "hm",
    "sigh", "cough", "sneeze", "breathing",
    # Whisper loves these on ambient noise
    "i love you", "thank you very much", "thank you so much",
    "i feel good", "hello", "hello hello", "hey", "hey hey",
    "good morning", "good night", "good evening", "good afternoon",
    "please", "excuse me", "i'm here", "here we go",
    "let's go", "come on", "oh my god", "oh my gosh",
    "i don't know", "i don't care", "whatever",
    # Garbled text patterns
    "oh come to kyrins", "dem firma buffer", "we're gonna get him",
    "hann svilcht böld", "hann svilcht böldbiand dragon", "bounth kitchen go away",
    "that's exactly what he said", "did you ugly me has a look", "kyrins",
    "svilcht", "böld", "biand", "bounth", "utveckl", "fáir fóssófár", "svíxtur hægku",
    "lavyrv hadlari", "lavyrv", "hadlari", "special airju", "airju",
}


def _is_hallucination(text: str) -> bool:
    """Check if transcription is a known Whisper hallucination or noise."""
    t = text.lower().strip().rstrip(".!?,;:")
    if t in _HALLUCINATIONS:
        return True
    
    # Check for encoding artifacts and non-ASCII corruption
    if any(ord(c) > 255 or c in "���" for c in text):
        return True
    
    # Check for mixed scripts (non-Latin characters that aren't punctuation)
    ascii_letters = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters < 0.8:
        return True
    
    # Check for gibberish patterns (too many consonants, no vowels)
    vowels = set("aeiouAEIOU")
    consonants = set("bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ")
    letters_only = ''.join(c for c in text if c.isalpha())
    if len(letters_only) >= 6:
        vowel_count = sum(1 for c in letters_only if c in vowels)
        consonant_count = sum(1 for c in letters_only if c in consonants)
        if consonant_count > 0 and vowel_count / (vowel_count + consonant_count) < 0.15:
            return True
    
    # Too short — likely noise
    if len(t) < 3:
        return True
    # Single word that isn't a command
    words = t.split()
    if len(words) <= 1:
        return True
    # Repeated phrases (e.g. "Thank you. Thank you. Thank you.")
    if len(words) >= 4:
        unique = set(words)
        if len(unique) <= 2:
            return True
        # Repeated sentence pattern: "X Y Z. X Y Z. X Y Z."
        # If unique words are ≤ 40% of total words, it's repetition
        if len(unique) / len(words) < 0.4:
            return True
    # All same word
    if len(set(words)) == 1:
        return True
    # Repeated short phrases (split on sentence boundaries)
    sentences = [s.strip().rstrip(".!?,;:") for s in t.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    if len(sentences) >= 2 and len(set(sentences)) == 1:
        return True
    return False


def _has_speech_energy(audio: np.ndarray, threshold: float = 0.01) -> bool:
    """Check if audio has enough energy to contain speech."""
    rms = np.sqrt(np.mean(audio ** 2))
    return rms > threshold


import concurrent.futures
_transcription_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")


def _transcribe_sync(audio: np.ndarray, sample_rate: int) -> str:
    """Synchronous transcription — runs in a thread pool with timeout."""
    model = _get_model()
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        language="en",
        vad_filter=True,
        condition_on_previous_text=False,  # Prevents decoder loops
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
    )

    parts = []
    for seg in segments:
        text = seg.text.strip()
        if seg.no_speech_prob > 0.6:
            continue
        if _is_hallucination(text):
            continue
        parts.append(text)

    result = " ".join(parts)
    if _is_hallucination(result):
        return ""
    return result


def transcribe_audio(audio: np.ndarray, sample_rate: int = 16000, timeout: float = 15.0) -> str:
    """Transcribe audio — Groq Whisper API primary, local Whisper fallback if API unavailable.

    Groq is faster and free. Local Whisper kicks in only when Groq fails/is offline.
    """
    if not _has_speech_energy(audio):
        return ""

    # Cap audio to 15 seconds max (conversational)
    max_samples = int(15.0 * sample_rate)
    if len(audio) > max_samples:
        audio = audio[:max_samples]

    # 1. Groq Whisper API — primary
    try:
        result = _transcribe_groq(audio, sample_rate)
        if result and not _is_hallucination(result):
            result = _boost_vocabulary(result)
            _save_for_training(audio, result, sample_rate)
            return result
    except Exception as e:
        print(f"[JARVIS] Groq Whisper error: {e}")

    # 2. Local Whisper — offline fallback only
    try:
        future = _transcription_pool.submit(_transcribe_sync, audio, sample_rate)
        result = future.result(timeout=timeout)
        if result and not _is_hallucination(result):
            result = _boost_vocabulary(result)
            _save_for_training(audio, result, sample_rate)
        return result
    except concurrent.futures.TimeoutError:
        print("[JARVIS] Whisper transcription timed out")
        return ""
    except Exception as e:
        print(f"[JARVIS] Whisper error: {e}")
        return ""


def _save_for_training(audio: np.ndarray, text: str, sample_rate: int):
    """Save audio+text pair for voice fine-tuning (background, best-effort)."""
    try:
        from src.speech.voice_collector import save_training_pair
        save_training_pair(audio, text, sample_rate)
    except Exception:
        pass  # Never let data collection break STT


def audio_bytes_to_numpy(audio_bytes: bytes) -> np.ndarray:
    """Convert audio bytes (webm/opus, mp4, wav, etc.) to 16kHz float32 mono numpy array.

    Uses ffmpeg via pipes — no temporary files.
    """
    cmd = [
        "ffmpeg", "-i", "pipe:0",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ar", "16000",
        "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=audio_bytes, capture_output=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.decode()}")
    if len(proc.stdout) == 0:
        raise RuntimeError("ffmpeg produced no audio output")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def transcribe_file(file_path: str) -> str:
    """Transcribe an audio file to text."""
    model = _get_model()
    segments, _ = model.transcribe(
        file_path,
        beam_size=5,
        language="en",
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments)

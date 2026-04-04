"""Voice input/output integration."""

from __future__ import annotations

from typing import Any, Callable, Optional


class VoiceManager:
    """Manages voice input (STT) and output (TTS) integration.

    Equivalent to useVoice React hook.
    """

    def __init__(
        self,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_voice_start: Optional[Callable] = None,
        on_voice_end: Optional[Callable] = None,
        enabled: bool = False,
    ):
        self._on_transcript = on_transcript
        self._on_voice_start = on_voice_start
        self._on_voice_end = on_voice_end
        self.enabled = enabled
        self.is_listening = False
        self.is_speaking = False

    def start_listening(self) -> None:
        if not self.enabled:
            return
        self.is_listening = True
        if self._on_voice_start:
            self._on_voice_start()

    def stop_listening(self) -> None:
        self.is_listening = False
        if self._on_voice_end:
            self._on_voice_end()

    async def speak(self, text: str) -> None:
        if not self.enabled:
            return
        self.is_speaking = True
        # TTS implementation would go here
        self.is_speaking = False

    def cancel_speech(self) -> None:
        self.is_speaking = False

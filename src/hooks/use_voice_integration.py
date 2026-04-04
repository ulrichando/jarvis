"""Voice integration combining STT, TTS, and voice UI."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .use_voice import VoiceManager


class VoiceIntegration:
    """Full voice integration: STT, TTS, wake word, voice activity detection.

    Equivalent to useVoiceIntegration React hook.
    """

    def __init__(
        self,
        on_submit: Optional[Callable[[str], None]] = None,
        on_input_change: Optional[Callable[[str], None]] = None,
        enabled: bool = False,
    ):
        self._on_submit = on_submit
        self._on_input_change = on_input_change
        self.voice = VoiceManager(
            on_transcript=self._handle_transcript,
            enabled=enabled,
        )

    def _handle_transcript(self, text: str) -> None:
        if self._on_input_change:
            self._on_input_change(text)

    def toggle(self) -> None:
        if self.voice.is_listening:
            self.voice.stop_listening()
        else:
            self.voice.start_listening()

    async def handle_response(self, text: str) -> None:
        await self.voice.speak(text)

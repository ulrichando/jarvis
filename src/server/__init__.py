"""JARVIS Server — HTTP + WebSocket + TTS web server."""

# Shared camera frame — updated by _handle_video_frame, read by the `see` tool
_latest_camera_frame = {"frame": None, "timestamp": 0}

# Provider error state — triggers setup wizard in frontend
_provider_error = {"failed": False, "errors": []}

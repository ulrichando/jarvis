# JARVIS headless voice agent (LiveKit worker) — CPU-only.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY proxy_token.py stt_local.py tts_edge.py agent.py ./
COPY scripts/ ./scripts/

# Bake models into the image so cold starts don't hit the network:
#  - livekit plugin assets (Silero VAD onnx etc.)
#  - the default faster-whisper model (override VOICE_STT_MODEL at
#    runtime -> that model downloads once to the container fs instead)
ARG STT_MODEL=small.en
RUN python agent.py download-files && \
    python -c "from faster_whisper import WhisperModel; WhisperModel('${STT_MODEL}', device='cpu', compute_type='int8')"

CMD ["python", "agent.py", "start"]

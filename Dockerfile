# ── Stage 1: Build React frontend ──────────────────────────────────────────────
# Alpine base: minimal packages = minimal attack surface.
# Update pin: docker pull node:22-alpine && docker inspect node:22-alpine | grep -i digest
FROM node:22-alpine@sha256:4d64b49e6c891c8fc821007cb1cdc6c0db7773110ac2c34bf2e6960adef62ed3 AS frontend-builder

WORKDIR /app/src/server/frontend

COPY src/server/frontend/package*.json ./
RUN npm ci --prefer-offline

COPY src/server/frontend/ ./
RUN npm run build
# Output → /app/src/server/static-react/

# ── Stage 2: Python backend ─────────────────────────────────────────────────────
# 3.13-slim is the most recent patched slim image.
# Update pin: docker pull python:3.13-slim && docker inspect python:3.13-slim | grep -i digest
FROM python:3.13-slim@sha256:32919c165487b858e003c8296c12920e8c94c243227f8546e23d4cc5aaa9e044

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV JARVIS_HOME=/data/.jarvis

WORKDIR /app

# Patch all base image vulnerabilities first, then install runtime deps.
# --no-install-recommends keeps the image lean.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends \
        # Audio
        ffmpeg \
        libportaudio2 \
        portaudio19-dev \
        libsndfile1 \
        # OpenCV headless runtime
        libgl1 \
        libglib2.0-0 \
        # OCR (pytesseract)
        tesseract-ocr \
        # Build tools for native Python extensions
        build-essential \
        # Healthcheck
        curl \
        # SSH client — needed to launch desktop app on owner's machine
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user ───────────────────────────────────────────────────────────────
# Port 8765 > 1024 so no privilege needed.
RUN groupadd -r jarvis && useradd -r -g jarvis -d /data -s /sbin/nologin jarvis

# ── Python dependencies ─────────────────────────────────────────────────────────
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir \
        opencv-python-headless \
        mss \
        pytesseract

# ── Frontend artifacts from stage 1 ────────────────────────────────────────────
COPY --from=frontend-builder /app/src/server/static-react/ ./src/server/static-react/

# ── Data volume & SSH directory for jarvis user ─────────────────────────────────
# /data/.ssh is where docker-compose mounts the SSH key (jarvis non-root home).
RUN mkdir -p /data/.jarvis /data/.ssh && \
    chmod 700 /data/.ssh && \
    chown -R jarvis:jarvis /data /app

USER jarvis

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8765/api/ready || exit 1

CMD ["python", "-m", "src.server.web_server"]

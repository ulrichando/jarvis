# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

# Match the exact path so Vite's outDir: '../static-react' resolves correctly
WORKDIR /app/src/server/frontend

COPY src/server/frontend/package*.json ./
RUN npm ci --prefer-offline

COPY src/server/frontend/ ./
RUN npm run build
# Output → /app/src/server/static-react/

# ── Stage 2: Python backend ────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV JARVIS_HOME=/data/.jarvis

WORKDIR /app

# Patch base image vulnerabilities + install system dependencies
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/*

# Install Python package — base deps only (no opencv-python with X11)
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir \
        opencv-python-headless \
        mss \
        pytesseract

# Copy freshly built frontend from stage 1
COPY --from=frontend-builder /app/src/server/static-react/ ./src/server/static-react/

# Persistent user data lives outside the image
RUN mkdir -p /data/.jarvis

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8765/api/ready || exit 1

CMD ["python", "-m", "src.server.web_server"]

# JARVIS voice-agent — headless container
# =========================================
#
# Containerizes JUST the voice-agent worker (the LLM/STT/TTS brain). The
# voice-client (audio capture/playback) and the Tauri desktop UI stay on
# the host — they need direct audio devices + X display. The container
# connects to either a containerised livekit-server (see docker-compose.yml)
# or a host-side one.
#
# Build:    docker build -t jarvis-voice-agent .
# Run:      docker compose up -d
# See:      setup/docker/README.md
#
# Multi-stage borrows the Astral uv image for a tiny, fast Python tool layer,
# then targets debian:13-slim for the runtime.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS uv_source

FROM debian:bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    JARVIS_HOME=/opt/jarvis-data

# System dependencies:
#   - build-essential, python3-dev, libffi-dev — native wheel builds
#   - ffmpeg — audio codecs for STT/TTS pipelines
#   - libsndfile1 — soundfile python lib (used by livekit-agents)
#   - procps — `ps` for the watchdog / debugging
#   - tini — reaps orphaned child processes (browser_use, etc.)
#   - curl + ca-certificates — used by checkers + uv-managed deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        libffi-dev \
        libsndfile1 \
        procps \
        python3-dev \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Bring in uv (the astral.sh fast Python package manager) from the uv image
COPY --from=uv_source --chmod=0755 /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

# Non-root user; UID can be overridden at runtime via JARVIS_UID to keep
# bind-mounted ~/.jarvis ownership clean across host ⇄ container.
RUN useradd --uid 10000 --create-home --home-dir /opt/jarvis-data jarvis

WORKDIR /opt/jarvis

# ── Python deps (cached layer) ─────────────────────────────────────────
# Copy ONLY requirements files first so the heavy install layer survives
# every source-only change. Re-runs only when requirements*.txt changes.
COPY --chown=jarvis:jarvis src/voice-agent/requirements.txt ./src/voice-agent/
RUN uv venv /opt/jarvis/src/voice-agent/.venv --python 3.13 \
    && uv pip install \
        --python /opt/jarvis/src/voice-agent/.venv/bin/python \
        -r /opt/jarvis/src/voice-agent/requirements.txt \
    && chown -R jarvis:jarvis /opt/jarvis

# ── Source code (the layer that changes most often) ────────────────────
# .dockerignore excludes .venv, node_modules, .git, etc.
COPY --chown=jarvis:jarvis . /opt/jarvis

# ── Runtime data volume ────────────────────────────────────────────────
# Mount ~/.jarvis here so keys.env, state.db, telemetry, plugins, skills
# persist across container restarts and are editable from the host.
RUN install -d -o jarvis -g jarvis /opt/jarvis-data \
    && install -d -o jarvis -g jarvis /opt/jarvis-state
VOLUME ["/opt/jarvis-data", "/opt/jarvis-state"]

# Telemetry + logs go here (mirror the host paths so symlinks/grep just work)
ENV XDG_DATA_HOME=/opt/jarvis-state \
    JARVIS_TURN_TELEMETRY_DB=/opt/jarvis-state/turn_telemetry.db

WORKDIR /opt/jarvis/src/voice-agent
USER jarvis

# Tini reaps zombies; the entrypoint handles bootstrap (mkdir, optional
# keys.env seed) then execs the voice-agent worker.
COPY --chown=jarvis:jarvis --chmod=0755 setup/docker/entrypoint.sh /usr/local/bin/jarvis-entrypoint.sh
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/jarvis-entrypoint.sh"]
CMD [".venv/bin/python", "jarvis_agent.py", "start"]

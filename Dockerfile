# ── Stage 1: Build React frontend ──────────────────────────────────────────────
FROM node:22-alpine@sha256:4d64b49e6c891c8fc821007cb1cdc6c0db7773110ac2c34bf2e6960adef62ed3 AS frontend-builder

WORKDIR /app/src/server/frontend
COPY src/server/frontend/package*.json ./
RUN npm ci --prefer-offline
COPY src/server/frontend/ ./
RUN npm run build

# ── Stage 2: App — fast layer, just code + frontend artifacts ──────────────────
# Base image has all system packages and Python deps pre-installed.
# Rebuild base only when pyproject.toml or system packages change.
FROM 10.10.0.123:3000/ulrich/jarvis-base:latest

ARG GIT_COMMIT=unknown
ENV JARVIS_GIT_COMMIT=$GIT_COMMIT

COPY src/ ./src/
COPY --from=frontend-builder /app/src/server/static-react/ ./src/server/static-react/

RUN mkdir -p /data/.jarvis /data/.ssh && \
    chmod 700 /data/.ssh && \
    chown -R jarvis:jarvis /data

USER jarvis

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8765/api/ready || exit 1

CMD ["python", "-m", "src.server.web_server"]

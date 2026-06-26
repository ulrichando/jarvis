# JARVIS web — production image.
# Runs the Next app + the node-pty server. Shells out to the `docker` CLI to
# spawn per-workspace sandbox containers — but ONLY against the restricted
# docker-socket-proxy (DOCKER_HOST in compose), never the raw host socket.
# Multi-stage: build native deps (node-pty) once, ship a non-root runtime.

# ── deps: install + compile native modules (node-pty) ───────────────────────
# Base: Debian trixie (13), not bookworm (12). Bookworm's core packages
# (libc6 2.36, perl-base 5.36, util-linux 2.38, zlib 1.2.13) carry ~144
# no-fix-in-stable Trivy CVEs; trixie ships 2.41 / 5.40 / 2.41 / 1.3.1, which
# resolve them. Native modules (better-sqlite3, node-pty) verified to build +
# load on trixie's glibc 2.41 before the bump.
FROM node:22-trixie-slim AS deps
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 make g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json* ./
RUN npm ci

# ── build: next build ───────────────────────────────────────────────────────
FROM deps AS build
COPY . .
# Browser terminal endpoint. NEXT_PUBLIC_* is INLINED at build time, so the
# domain must be known here. Route through Caddy's /pty (not the raw :8772
# port). Pass at build: `--build-arg NEXT_PUBLIC_PTY_URL=wss://jarvis.example.com/pty`
# (compose sets it from .env.production via build.args).
ARG NEXT_PUBLIC_PTY_URL=""
ENV NEXT_PUBLIC_PTY_URL=$NEXT_PUBLIC_PTY_URL
# Build-time placeholders. better-auth (auth.ts) + others validate env at
# MODULE scope, but `next build` is static analysis — no real DB/auth is ever
# contacted. Real values are injected at RUNTIME via compose. These only need
# to be syntactically valid (a parseable URL), never real.
ENV DATABASE_URL=postgresql://build:build@127.0.0.1:5432/build \
    BETTER_AUTH_SECRET=build-time-placeholder-overridden-at-runtime \
    BETTER_AUTH_URL=http://localhost:3000 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── runtime: slim + docker CLI client only, non-root ────────────────────────
FROM node:22-trixie-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
# docker CLI *client* (no daemon) — talks to the socket-proxy over TCP.
# Pinned to a build whose Go stdlib is patched: 27.5.1 shipped Go 1.22.11
# (CVE-2025-68121 + siblings, critical); 29.6.1's static binary is Go 1.26.4.
# Verify a bump with `go version <extracted docker binary>` before raising it —
# the apt build's Go version differs from the static tarball's.
ARG DOCKER_CLI_VERSION=29.6.1
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
# Non-root runtime user.
RUN useradd -m -u 10001 jarvis
COPY --from=build --chown=10001:10001 /app ./
# Strip build-time-only tooling from the runtime image. drizzle-kit (the
# migration CLI, run OUTSIDE the container per docs/runbook/deploy-online.md)
# drags in old esbuild binaries (Go 1.18/1.23 — Trivy criticals CVE-2024-24790
# + CVE-2025-68121) through better-auth's optional peer dep. start:container
# only runs `next start` + the pty-server, neither of which imports it, so
# removing it + its esbuild-carrying siblings (@esbuild-kit, tsx) is safe and
# clears those image CVEs. The direct prod `esbuild` (artifact bundling, a
# current build) is untouched.
RUN rm -rf node_modules/drizzle-kit node_modules/@esbuild-kit node_modules/tsx \
           node_modules/.bin/drizzle-kit node_modules/.bin/tsx
USER 10001
EXPOSE 3000
# tini reaps zombies. start:container binds next + the pty-server to 0.0.0.0 so
# Caddy can reach them over the docker network (the default `start` binds
# 127.0.0.1 = container-loopback only). The container is network-isolated and
# only Caddy fronts it, so 0.0.0.0 here is safe. JARVIS_PTY_HOST is set in compose.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["npm", "run", "start:container"]

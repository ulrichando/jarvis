# JARVIS web — production image.
# Runs the Next app + the node-pty server. Shells out to the `docker` CLI to
# spawn per-workspace sandbox containers — but ONLY against the restricted
# docker-socket-proxy (DOCKER_HOST in compose), never the raw host socket.
# Multi-stage: build native deps (node-pty) once, ship a non-root runtime.

# ── deps: install + compile native modules (node-pty) ───────────────────────
# Base: Alpine (musl), not Debian. Debian's core packages (perl-base, glibc,
# util-linux, …) carry ~150 Trivy CVEs that are mostly NO-fix-in-stable —
# including 2 critical perl-base CVEs unpatched in every Debian release. Alpine's
# musl + minimal package set drops that OS-CVE floor to a handful. Native modules
# (better-sqlite3, node-pty) verified to build AND run on alpine/musl before the
# switch; esbuild resolves its platform binary via npm optionalDeps.
FROM node:24-alpine AS deps
WORKDIR /app
RUN apk add --no-cache python3 make g++
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

# ── runtime: minimal alpine + docker CLI client only, non-root ──────────────
FROM node:24-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
# docker CLI *client* (no daemon) — talks to the socket-proxy over TCP. From
# Alpine apk (apk-maintained + patched; no glibc static download needed on musl).
# tini reaps zombies; ca-certificates for outbound TLS.
RUN apk add --no-cache docker-cli tini ca-certificates
# Non-root runtime user (Alpine's adduser).
RUN adduser -D -u 10001 jarvis
COPY --from=build --chown=10001:10001 /app ./
# Strip build-time-only tooling from the runtime image. drizzle-kit (the
# migration CLI, run OUTSIDE the container per docs/runbook/deploy-online.md)
# drags in old esbuild binaries through better-auth's optional peer dep.
# start:container only runs `next start` + the pty-server, neither of which
# imports it, so removing it + its esbuild-carrying siblings (@esbuild-kit, tsx)
# is safe. The direct prod `esbuild` (artifact bundling) is untouched.
RUN rm -rf node_modules/drizzle-kit node_modules/@esbuild-kit node_modules/tsx \
           node_modules/.bin/drizzle-kit node_modules/.bin/tsx
USER 10001
EXPOSE 3000
# tini reaps zombies. start:container binds next + the pty-server to 0.0.0.0 so
# Caddy can reach them over the docker network (the default `start` binds
# 127.0.0.1 = container-loopback only). The container is network-isolated and
# only Caddy fronts it, so 0.0.0.0 here is safe. JARVIS_PTY_HOST is set in compose.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["npm", "run", "start:container"]

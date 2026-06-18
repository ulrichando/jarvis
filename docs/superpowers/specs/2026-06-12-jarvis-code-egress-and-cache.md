# /code container hardening — egress proxy, network levels, setup caching

Status: SPEC (build-ready; needs a docker-equipped implement+verify pass)
Author: 2026-06-12
Context: closes the last claude.ai/code parity items after the app-layer work
(diff/review/PR/CI/env-config/teleport) landed. See decisions-pending.md §12.

## Why this is a spec, not shipped code

These three are **docker infrastructure** (bridge networks, an HTTP CONNECT
allowlist proxy, `docker commit`/restore). They cannot be verified in the
authoring sandbox, and a subtle error breaks the *working* `/code` feature
(the child→web-app callback, or a poisoned cache image). They must be built
and tested on a box with docker.

Value note: the egress proxy is a **multi-tenant** hardening feature. On the
current single-user localhost box — which per `CLAUDE.local.md` already accepts
the mic→root blast radius — it adds ~no security. Build it when the box
becomes multi-user, is exposed beyond localhost, or the threat model changes.
Setup caching is a latency win, independent of the threat model.

## The core constraint

Today the in-container child posts back to the web app at `127.0.0.1:3000`,
which works only because the container runs `--network=host`. ANY network
isolation must preserve that callback. Solution used throughout this spec:
run the container on a docker bridge network with
`--add-host=host.docker.internal:host-gateway`, and rewrite the child's
`--sdk-url` base from `127.0.0.1:3000` → `host.docker.internal:3000`
(`containers.ts` builds `sdkUrl`; thread the host through `opts.baseUrl`).
`NO_PROXY` must include `host.docker.internal,localhost,127.0.0.1` so the
callback bypasses the egress proxy.

## 1. Network access levels (env config)

Add to `EnvironmentConfig`: `networkLevel: "full" | "trusted" | "custom" | "none"`
(default `"full"`). Surface in the env-config modal as a select + a
`customAllowlist: string[]` textarea (shown only for `custom`). Persist in
`config_json` (already exists).

- `full` (DEFAULT): exact current behavior — `--network=host`, no proxy. Zero
  regression; everything below is opt-in.
- `trusted`: bridge network + egress proxy allowing the default registries
  (npm, PyPI, RubyGems, crates.io, GitHub, the model proxy host) and denying
  the rest. Mirror claude.ai's default-allowed list.
- `custom`: `trusted` + the user's `customAllowlist` domains.
- `none`: bridge network + proxy that denies all egress (only the callback
  host route works).

## 2. Egress allowlist proxy

Per session (or a shared per-environment) **squid** container:
- Image: pin `ubuntu/squid:<digest>` (or bake squid into a `jarvis-egress`
  image built alongside the workbench image; preferred — avoids a runtime pull).
- Generate `squid.conf` from the level: `acl allowed dstdomain .github.com
  .npmjs.org files.pythonhosted.org …; http_access allow allowed; http_access
  deny all`. CONNECT (HTTPS) is filtered by `dstdomain` on the CONNECT target.
- Lifecycle: `docker network create jarvis-net-<sid>`; run the proxy on it;
  run the workbench on it; set child env `HTTP_PROXY`/`HTTPS_PROXY`/`http_proxy`/
  `https_proxy=http://<proxy>:3128`, `NO_PROXY=host.docker.internal,localhost,127.0.0.1`.
  Reap the proxy + network in `stopContainerSession`.
- git/gh honor `HTTPS_PROXY`; npm/pip honor `*_proxy`. Verify each in the test
  plan (some tools need explicit proxy config).

## 3. Setup-snapshot caching

Cache the result of the setup scripts so later sessions skip them (~claude.ai's
7-day filesystem-snapshot cache). Env-gate `JARVIS_CODE_SETUP_CACHE=1` (default
OFF) so it ships dark.

- Cache key: `jarvis-workbench-cache:<envId>-<sha1(setupScript + .jarvis/setup.sh)>`.
  Hashing the scripts invalidates the cache when they change.
- Miss: clone → env setup → repo setup (current flow) → `docker commit <name>
  <cacheTag>` (excludes the bind-mounted CLI, which is RO and re-mounted).
- Hit: `docker run` from `<cacheTag>` instead of the base image; SKIP clone +
  both setup steps; **re-run the git identity + credential config** (the cached
  creds may be stale/rotated) and `git -C <workdir> fetch origin &&
  git reset --hard "$base" && git clean -fd` to freshen the repo. Then launch
  the CLI as normal.
- TTL/eviction: prune cache images older than 7d in the nightly timer; cap
  total cache size. A poisoned/broken cache falls back by deleting the tag.

## Verification plan (docker box)

1. `full` level + no cache: identical to today (regression gate — the 133 web
   tests already cover the host-network path via the injected `proxyHealthy`).
2. `trusted`: `npm install` succeeds; `curl https://evil.example` is blocked;
   the child callback still reaches the web app (session runs end to end).
3. `none`: callback works, all other egress blocked.
4. cache hit: second session for an env with a setup script starts in <5s and
   skips the setup status steps; repo reflects the latest default branch.
5. cleanup: archiving a session reaps its container, proxy, and network.

## Rollback

Everything is opt-in. `networkLevel` defaults to `full` (current behavior) and
`JARVIS_CODE_SETUP_CACHE` defaults off, so shipping the code is inert until a
user opts in — and each path falls back to the current behavior on error.

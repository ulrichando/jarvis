# Contributing to JARVIS

JARVIS is a personal project. External contributions are welcome, but please
open an issue or discussion before starting significant work so we can align
on direction.

---

## Repository layout

| Subtree | Path |
|---|---|
| Voice agent (Python) | `src/voice-agent/` |
| Desktop UI (Tauri/Rust + React) | `src/desktop-tauri/` |
| Web app (Next.js) | `src/web/` |
| CLI agent (TypeScript/Bun) | `src/cli/` |
| Android app (Kotlin/Compose + NDK) | `src/android/` |

Each subtree is an independent build. Work in only the subtree relevant to
your change. **`src/cli/` is treated as a separate codebase** — do not
modify it when working on desktop, voice-agent, or web without explicit sign-off.

---

## Build and test — per subtree

### Voice agent (`src/voice-agent/`)

```sh
cd src/voice-agent

# Set up venv (first time)
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Run the full test suite
.venv/bin/python -m pytest tests/

# Run a filtered subset
.venv/bin/python -m pytest tests/ -k "memory"

# Restart the live service (only after checking telemetry DB for active sessions)
systemctl --user restart jarvis-voice-agent.service
```

Tests live under `src/voice-agent/tests/` (~200 files, ~3,000 tests, ~70s full run).

### Desktop UI (`src/desktop-tauri/`)

```sh
cd src/desktop-tauri

# Install JS deps
npm install

# Dev server (hot-reload Vite + Tauri window)
npm run tauri dev

# Check JS/TS (catches syntax + import errors, ~7s)
npm run build

# Release build — BOTH steps required to re-embed dist/ into the Tauri binary
npm run build
cargo build --release
```

`npm run build` alone does NOT ship JS changes into the binary.

### Web app (`src/web/`)

```sh
cd src/web

# Install deps
bun install

# Type-check / lint
bun run build

# Tests
bun test
```

### CLI agent (`src/cli/`)

```sh
cd src/cli

bun install
bun test
bun run build   # if a build step is configured
```

---

## Commit conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes:
  `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `style`.
- Keep the subject line under 72 characters.
- **No `Co-Authored-By` trailers.** No AI-attribution lines in commit messages
  or PR descriptions.
- One logical change per commit. Squash "fix typo" noise before opening a PR.

Examples:

```
feat(voice-agent): add echo-aware barge-in gate
fix(desktop): resolve chat spinner timeout on bridge disconnect
docs: add env-reference.md (complete JARVIS_* flag manifest)
chore: gitignore NDK build artifacts in src/android/app/.cxx/
```

---

## Regression-prevention rules (summary)

Before touching code, declare scope (`SCOPE:`, `OUT:`, `WHY OUT:`). This
makes unintended side-edits visible before they happen.

Before editing a function, read:
- the file that contains it,
- its immediate callers (grep the symbol across the repo),
- its immediate callees within the same subtree,
- any sanitizer or monkey-patch in `CLAUDE.md` that touches its path.

Verify before claiming done:
- Voice-agent edit → `pytest` passes.
- Desktop edit → `npm run build` passes (and `cargo build --release` for
  release builds).
- Web/CLI edit → that subtree's build/test command passes.

Do not delete code you think is unused without grepping the whole repo,
including `bin/`, `setup/systemd/`, `scripts/`, and `src/cli/src/bridge/`.

**Full rules:** [`.claude/rules/regression-prevention.md`](.claude/rules/regression-prevention.md)

---

## Environment variables

All `JARVIS_*` flags are documented in [`docs/env-reference.md`](docs/env-reference.md).
Required API keys and how to populate `.env` are covered in the
[`README.md`](README.md) prerequisites section.

---

## Security issues

See [`SECURITY.md`](SECURITY.md) — please do not open public issues for
security vulnerabilities.

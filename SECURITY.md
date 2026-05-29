# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately via one of these channels:

- **GitHub private security advisory:** open a draft advisory at
  `https://github.com/ulrichando/jarvis/security/advisories/new`
- **Email:** contact the maintainer directly at the address on their GitHub profile.

You should receive an acknowledgement within 48 hours and a status update
within 7 days. If you do not hear back, follow up via the same channel.

Please include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce (proof-of-concept script or command sequence preferred).
- Affected component (voice-agent, desktop, CLI, bridge, web, Android).
- Any suggested mitigations you have identified.

We will coordinate a fix, determine the disclosure timeline, and credit you
in the release notes unless you prefer to remain anonymous.

## Supported Versions

JARVIS is currently pre-1.0. Security fixes are applied to the `master`
branch only. There are no separately maintained release branches at this time.

| Branch / version | Supported |
|---|---|
| `master` (latest) | Yes |
| Older commits / forks | No — please update |

## Security Model

### Execution context

JARVIS is a **personal, single-user, locally-deployed assistant**. It is
designed for use on a host you control, by the same person who installed it.
It is **not** a multi-tenant service.

The voice agent, desktop UI, CLI agent, and bridge all run with the
privileges of the invoking OS user. This is intentional: JARVIS needs to
read/write files, launch applications, and interact with the desktop on your
behalf. Deploy only on a host you administer and trust.

**Threat model — mic input and prompt injection are in scope.** A malicious
actor who can reach your microphone, inject text into a conversation, or
manipulate data sources that JARVIS reads (web pages, files, emails) could
in principle cause JARVIS to execute unintended commands as your user. Treat
any system that handles ambient audio as you would treat your shell.

Mitigations in place:
- The sanitizer layer (`src/voice-agent/sanitizers/`) strips tool-call
  shapes from LLM reply text before they can be acted upon.
- The confab detector (`src/voice-agent/confab_detector.py`) refuses to
  record "success" claims that lack real tool-result evidence.
- The `terminal` tool only allows a defined named-action surface; it is
  not a raw `eval` of arbitrary LLM output.
- The `computer_use` tool audit-logs every action to the telemetry DB
  and screenshot store.
- The auto-mod loop (`JARVIS_AUTOMOD_ENABLED`) is gated, defaults to
  shadow mode, and enforces a hard blocklist that covers all security-
  critical files; auto-mod proposals require human merge.

### Secrets and credentials

API keys and other secrets live in:

| Location | Description |
|---|---|
| `src/voice-agent/.env` | Voice-agent LLM/STT/TTS provider keys |
| `~/.jarvis/*.env` | Per-user key overrides (loaded at runtime) |
| `~/.jarvis/local-api-token.env` | Bridge bearer token |

**These files must never be committed.** They are listed in `.gitignore`.
If a secret is accidentally committed, rotate it immediately using the
provider's key-management console, then follow the instructions in
`docs/runbook/credential-rotation.md` and `docs/runbook/git-history-scrub.md`
to purge it from git history before the commit is pushed or shared.

Recommended filesystem permissions:

```sh
chmod 600 src/voice-agent/.env
chmod 600 ~/.jarvis/*.env
```

### Local bridge / API surface

The bridge server (`src/cli/src/bridge/server.ts`) binds to
`127.0.0.1:8765` (loopback only). It is **not exposed to the network**
by default. Bearer-token authentication is enforced when
`JARVIS_REQUIRE_LOCAL_AUTH=1` is set (the installer sets this by default).

The web app (`src/web/`) is a development-only server. It should not be
exposed to untrusted networks without adding authentication.

### Data at rest

Conversation history, memory entries, and telemetry are stored locally in
SQLite databases under `~/.jarvis/` and `~/.local/share/jarvis/`. These
files are not encrypted at rest by default. See
`docs/runbook/encryption-at-rest.md` for guidance on applying filesystem-
level encryption if required by your threat model.

### X11 / screen access

The `computer_use` tool requires an X11 session and uses `xdotool`,
`scrot`, and the Anthropic computer-use API surface to interact with your
desktop. This is intentional — it is how JARVIS performs GUI automation.
Confine the assistant to contexts where you are comfortable with
programmatic desktop control.

### Dependency security

The `.github/workflows/security-audit.yml` workflow runs automated
dependency audits on each push. Check the Actions tab for current status.
If you discover a CVE in a pinned dependency, open a normal (public) issue
referencing the CVE so it can be prioritised alongside other work.

# JARVIS Repair Kit

An enterprise-grade engineering operation in a folder. Drop this into your JARVIS repo (or anywhere your AI coding tool can read it) and use it to run a long-lived, multi-session repair effort that holds itself to FAANG-grade standards.

## What this is

This kit instantiates a virtual engineering organization — Orchestrator, Product Manager, Principal Architect, domain engineers, Security, QA, SRE, and an Adversarial Reviewer — that operates under a written charter, respects a scope you declare, and persists state across sessions so the work survives context-window resets.

It is designed for **long-running** repair (weeks to months), not one-shot fixes. If you only need a single bug squashed, this is overkill.

## The files

```
JARVIS-REPAIR/
├── README.md                       ← you are here
├── 00-MASTER-PROMPT.md             ← paste this at the start of every session
├── 01-ENGINEERING-CHARTER.md       ← team, principles, workflow, standards (the contract)
├── 02-SCOPE.md                     ← YOU fill this out before starting; defines what is in/out
├── 03-STATE.md                     ← living state of the repair; updated every session
├── handoffs/                       ← end-of-session notes; bootstrap reads the latest
├── decisions/                      ← ADRs (one file per architectural decision)
├── rfcs/                           ← RFCs (one file per non-trivial design)
└── templates/
    ├── RFC.md                      ← design doc template
    ├── ADR.md                      ← architecture decision record
    ├── POSTMORTEM.md               ← when something breaks during repair
    ├── PATCH.md                    ← patch submission with self-review
    └── SESSION-HANDOFF.md          ← end-of-session protocol
```

## How to use it (start to finish)

### One-time setup
1. Copy this `JARVIS-REPAIR/` directory into your repo (or anywhere your AI coding tool — Claude Code, Cursor, etc. — can read files).
2. **Fill out `02-SCOPE.md`.** This is the most important step. The team treats this as binding. If CLI is in good shape and you don't want it touched, say so here.
3. Initialize `03-STATE.md` by replacing the `<placeholders>` with your actual starting state, or leave it as-is and let the team populate it during the first session's Phase 1.

### Every session
1. Open a fresh chat (Claude Code session, Cursor, claude.ai with Opus 4.7 — anywhere with file-read capability).
2. Paste the contents of `00-MASTER-PROMPT.md` as your first message.
3. The team executes the bootstrap: reads the charter, scope, state, and the latest handoff, then posts a Session Kickoff and waits for you.
4. Work the day's items. Approve plan changes when asked. Push back when something feels wrong.
5. At the end of the session, the team writes a handoff to `handoffs/session-<N>.md`. **Save / commit the updated files.**

### Across sessions
- Decisions get written to `decisions/ADR-<n>-<slug>.md`.
- Non-trivial designs get an RFC at `rfcs/RFC-<n>-<slug>.md` before any code is written.
- Incidents during repair (a fix that broke something) get a postmortem.
- The Issue Register and Work Item registry in `03-STATE.md` are kept current as the source of truth.

## What "10/10" means here

This kit is built around the seven failure modes that kill long-running AI engineering efforts:

1. **Scope drift** → fixed by binding `02-SCOPE.md` and a hard "scope guard" rule in the charter.
2. **Context loss across sessions** → fixed by the bootstrap sequence and `handoffs/`.
3. **Hallucinated file contents** → fixed by the charter requiring agents to actually read files via available tools, never guess.
4. **Mega-diffs no one can review** → fixed by the patch-size cap and Adversarial Reviewer.
5. **Vague "best practices"** → fixed by operationalized SLOs and a measurable Definition of Done in the charter.
6. **Sycophancy / inability to push back on bad framing** → fixed by an explicit "challenge the user's framing when evidence warrants it" principle.
7. **Lost decisions / repeated debates** → fixed by ADRs and the decisions ledger.

If you find a failure mode this kit doesn't handle, you've found a v2 issue. Open it as a postmortem on the kit itself.

## Recommended runtime

**Claude Code** at the repo root with Opus 4.7. The team can then actually read files, run tests, grep the codebase, and verify claims. Running this in a chat without file-read tools defeats most of the rigor.

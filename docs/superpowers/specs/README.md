# Design specs index

All dated spec files under this directory, grouped chronologically.
Do NOT move spec files — the cross-links in `CLAUDE.md` and sibling docs
use relative paths rooted here.

Tombstoned/superseded specs are marked. Do not rebuild the described
systems unless the tombstone note is explicitly cleared.

---

## 2026-04-23 — foundation

| File | Status | Summary |
|---|---|---|
| `2026-04-23-app-builder-ui-redesign-design.md` | Archived | App-builder UI redesign (early phase) |
| `2026-04-23-jarvis-voice-like-claude-design.md` | Archived | Voice intelligence parity goal; seeded the 10-axis rubric |

## 2026-04-27

| File | Status | Summary |
|---|---|---|
| `2026-04-27-jarvis-personality-design.md` | Archived | Early personality / persona design |
| `2026-04-27-jarvis-silence-fix-design.md` | Archived | First-turn silence fix |

## 2026-04-28

| File | Status | Summary |
|---|---|---|
| `2026-04-28-desktop-computer-use-design.md` | Archived | Desktop computer-use initial design; superseded by the 2026-05-18 parity spec |

## 2026-04-29

| File | Status | Summary |
|---|---|---|
| `2026-04-29-continuous-screen-watching-design.md` | Archived | Continuous screen observer design |
| `2026-04-29-design-rubric-results.md` | Archived | Voice-intelligence rubric scoring snapshot |
| `2026-04-29-design-tab-overhaul-design.md` | Archived | Desktop design-tab overhaul |
| `2026-04-29-jarvis-maya-class-speech-design.md` | Archived | Maya-class speech quality target |
| `2026-04-29-news-widget-design.md` | Archived | News widget (desktop) |

## 2026-04-30

| File | Status | Summary |
|---|---|---|
| `2026-04-30-jarvis-extension-browser-control-design.md` | Archived | Browser extension control (browser_ext tools); shipped in feat/ext-browser-control-v3 |
| `2026-04-30-voice-intelligence-rubric.md` | Active reference | 10-axis /100 voice-intelligence rubric definition |

## 2026-05-02

| File | Status | Summary |
|---|---|---|
| `2026-05-02-jarvis-voice-polish-design.md` | Archived | Voice-mode polish spec |

## 2026-05-03

| File | Status | Summary |
|---|---|---|
| `2026-05-03-jarvis-event-hub-design.md` | Active reference | Event hub (Redis Streams + hub.py) architecture |
| `2026-05-03-jarvis-memory-layer-design.md` | Superseded | Original memory layer design — superseded by `2026-05-08-anti-gaslighting-memory-design.md` (4-layer v2 architecture) |
| `2026-05-03-jarvis-retire-convex-design.md` | Completed | Convex retirement plan — shipped; `src/convex/` is now a dead stub |
| `2026-05-03-jarvis-unified-settings-design.md` | Active reference | Unified settings / hub design |

## 2026-05-04

| File | Status | Summary |
|---|---|---|
| `2026-05-04-jarvis-voice-resilience-design.md` | Active reference | Circuit breaker, idle timeout, reconnect ladder, watchdog |
| `2026-05-04-supervisor-langgraph-design.md` | **SUPERSEDED / TOMBSTONED — do not rebuild** | LangGraph alt-supervisor spec. The LangGraph supervisor was built (2026-05-04) and then deleted in commit `f38c358` (2026-05-10) after the 10/10 refactor. `pipeline/turn_graph.py` remains as the **slow-path dispatcher** (not a supervisor replacement) behind the `JARVIS_GRAPH_DISABLED=1` kill-switch. Do not rebuild a LangGraph supervisor. |
| `2026-05-04-truth-grounded-supervisor-design.md` | Active reference | Confab detector + truth-grounded supervisor rules |

## 2026-05-05

| File | Status | Summary |
|---|---|---|
| `2026-05-05-kimi-k2-modes-web-design.md` | Superseded / broken | Kimi K2.6 voice-supervisor design. Kimi voice is broken (`web_search not in request.tools`); gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`. Do not re-enable without a fix. |

## 2026-05-07

| File | Status | Summary |
|---|---|---|
| `2026-05-07-barge-in-truncation-design.md` | Superseded | Barge-in truncation first attempt — superseded by the 2026-05-18 full barge-in fix |
| `2026-05-07-regression-prevention-design.md` | Superseded | Regression-prevention process design — the rules landed in `.claude/rules/regression-prevention.md`; this spec is archival |

## 2026-05-08

| File | Status | Summary |
|---|---|---|
| `2026-05-08-anti-gaslighting-memory-design.md` | **Active (load-bearing)** | 4-layer memory v2 architecture: auto-extractor + recall force-routing + denial detector + remember() backup |
| `2026-05-08-memory-consolidator-design.md` | Active reference | Memory consolidator design |
| `2026-05-08-voice-agent-desktop-tauri-review.md` | Archived | Desktop/voice-agent review snapshot |
| `2026-05-08-voice-tool-audit-inventory.md` | Archived | Voice tool audit inventory (pre-rebuild) |

## 2026-05-09

| File | Status | Summary |
|---|---|---|
| `2026-05-09-cli-voice-functionality-design.md` | Active reference | CLI voice functionality design |
| `2026-05-09-jarvis-drop-butler-register-design.md` | Completed | Drop butler "sir" register — shipped; all occurrences removed across codebase |
| `2026-05-09-jarvis-web-ccr-bridge-design.md` | Active reference | Web CCR bridge design |
| `2026-05-09-voice-agent-review-closeout-design.md` | Archived | Voice-agent review closeout |

## 2026-05-12

| File | Status | Summary |
|---|---|---|
| `2026-05-12-jarvis-self-evolution-design.md` | Superseded | Original self-evolution design — superseded by the 2026-05-20 rebuild spec |

## 2026-05-17

| File | Status | Summary |
|---|---|---|
| `2026-05-17-browser-cdp-fallback-design.md` | Active reference | Browser CDP fallback design |

## 2026-05-18

| File | Status | Summary |
|---|---|---|
| `2026-05-18-barge-in-interrupt-fix-design.md` | **Active (load-bearing)** | Full barge-in fix: VAD-direct mode, Deepgram primary STT, TTS upstream-cancel, `min_words=0` |
| `2026-05-18-cua-password-check-failopen-design.md` | Active reference | Computer-use password-check fail-open |
| `2026-05-18-jarvis-computer-use-parity-design.md` | **Active (load-bearing)** | Computer-use parity with Claude AI; X11 only; audit trail |

## 2026-05-19

| File | Status | Summary |
|---|---|---|
| `2026-05-19-confab-defense-in-depth-design.md` | **Active (load-bearing)** | Confab detector L2 fix: structured tool_result required for evidence |
| `2026-05-19-echo-cancellation-cascade-design.md` | Active reference | Echo cancellation cascade design |

## 2026-05-20

| File | Status | Summary |
|---|---|---|
| `2026-05-20-aec-cascade-completion-design.md` | Active reference | AEC cascade completion |
| `2026-05-20-echo-aware-bargein-gate-design.md` | Active reference | Echo-aware barge-in gate |
| `2026-05-20-jarvis-between-turn-scheduler-design.md` | Active reference | Between-turn scheduler |
| `2026-05-20-jarvis-self-improvement-rebuild-design.md` | **Active (load-bearing)** | Hermes→JARVIS rebuild: torn-down subagents, soul extraction, direct tool registry only |
| `2026-05-20-jarvis-skill-loop-design.md` | Active reference | Skill loop design |
| `2026-05-20-jarvis-soul-design.md` | **Active (load-bearing)** | Soul extraction: soul.md as slot-#1 system prompt; persona/ops separation |

## 2026-05-22

| File | Status | Summary |
|---|---|---|
| `2026-05-22-memory-provider-turn-loop-design.md` | Active reference | Memory provider turn-loop design |

## 2026-05-23

| File | Status | Summary |
|---|---|---|
| `2026-05-23-jarvis-soul-enterprise-design.md` | Active reference | Soul / enterprise hardening design |
| `2026-05-23-windows-install-phase1-design.md` | **Active (load-bearing)** | Windows installer Phase 1–3 cross-platform refactor |

## 2026-05-24

| File | Status | Summary |
|---|---|---|
| `2026-05-24-jarvis-memory-and-procedure-loop-design.md` | Active reference | Memory + procedure capture loop |
| `2026-05-24-jarvis-source-code-self-mod-design.md` | **Active (load-bearing)** | Auto-mod loop: gated, audited, reversible; hard blocklist; daily cap |
| `2026-05-24-pre-tts-confab-gate-design.md` | Active reference | Pre-TTS confab gate (first pass) |
| `2026-05-24-tray-chat-panel-design.md` | Active reference | Tray chat panel design |

## 2026-05-27

| File | Status | Summary |
|---|---|---|
| `2026-05-27-automod-error-driven-branch-design.md` | Active reference | Auto-mod error-driven branch |
| `2026-05-27-jarvis-kiosk-mode-design.md` | Superseded | Kiosk mode v1 — superseded by v2 (2026-05-28) |
| `2026-05-27-post-tool-reply-gate-and-indicator-heartbeat.md` | Active reference | Post-tool reply gate + indicator heartbeat |
| `2026-05-27-pre-tts-confab-gate-pattern-coverage.md` | **Active (load-bearing)** | Pre-TTS confab gate extended pattern coverage |
| `2026-05-27-voice-agent-subagent-dispatch.md` | **Active (load-bearing)** | Out-of-process dispatch_agent tool (explore/researcher/code_reviewer/plan subagents) |

## 2026-05-28

| File | Status | Summary |
|---|---|---|
| `2026-05-28-automod-auto-merge-rollback-design.md` | Active reference | Auto-mod auto-merge + rollback design |
| `2026-05-28-jarvis-french-codeswitch-design.md` | **Active (load-bearing)** | French/English code-switch support |
| `2026-05-28-jarvis-kiosk-mode-v2-design.md` | **Active (load-bearing)** | Kiosk mode v2 (supersedes v1) |

---

## Conventions

- Spec files follow the pattern `YYYY-MM-DD-<slug>-design.md`.
- Plan files (in `docs/superpowers/plans/`) follow the same pattern with
  `-plan.md` suffix. Plans describe the implementation steps; specs describe
  the design.
- **Active (load-bearing)** — the code was built to this spec and the spec
  is still the authoritative reference for that subsystem's design decisions.
- **Active reference** — shipped and still valid; useful background but the
  code may have drifted in details.
- **Archived** — shipped; kept for historical reference only.
- **Superseded** — a later spec replaces this one; do not implement the old design.
- **TOMBSTONED** — the described system was explicitly deleted or is known
  broken; do not rebuild without explicit sign-off.

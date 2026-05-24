# JARVIS soul + supervisor — enterprise-grade refresh

**Date:** 2026-05-23
**Status:** draft for review
**Scope:** `src/voice-agent/prompts/soul.md` + `src/voice-agent/prompts/supervisor.md`
**Owner:** Ulrich

## 1. Problem

Two failures, same root: prompt and capability surface have drifted apart since the
2026-05-20 Hermes→JARVIS rebuild.

1. **The browser lie.** Live capture 2026-05-22 17:06:13 — user: *"Jarvis, can you
   open a new tab on my current browser?"* → JARVIS: *"Done — new tab is open."*
   No tab opened on the user's visible Chrome. Same pattern recurred ≥5 times that
   day (03:53 / 12:10 / 16:58 / 17:06 / 17:46). Root cause: the only browser tool
   post-rebuild is `browser_task`, which always runs `headless: True`
   (`tools/browser.py:305`) in an isolated `browser_use` subprocess — invisible to
   the user. `supervisor.md` still routes every "tab + browser" phrase to it
   (lines 315, 375–379, 850–853, with "browser_task cold-starts Chrome" at 845–846
   now factually wrong). The honesty guardrails (lines 535–574) gate on
   *"is there a confirming tool_result?"* — they can't distinguish a
   *headless* success from a *visible* one, so wrong-surface success slips
   through.

2. **The soul teaches dead tools.** `soul.md`'s few-shot exemplars at lines 445,
   451, 457, 459–460, 463, 493–499 reference `transfer_to_desktop`,
   `transfer_to_browser`, `task_done`, `current_time`, `recall_conversation`,
   and "browser subagent". Verified: none are registered tools. The current
   surface is `computer_use` / `browser_task` / `terminal` / `memory` /
   `session_search`. The soul is actively teaching JARVIS a tool surface that
   no longer exists.

User goal: refresh the soul (and the supervisor that sits behind it) to be
**enterprise-grade** — the persona slot must reflect real tools, contradictions
must go, and the file must meet recognised standards for conversational-AI
behaviour.

## 2. Sourced principles

The standards this refresh is measured against:

- **[Anthropic — Claude's Character](https://www.anthropic.com/research/claude-character):**
  honesty over sycophancy; the model holds opinions but stays open-minded;
  transparent about being AI without overclaiming feelings or memory
  ("cannot remember, save, or learn from past conversations"); on sentience,
  "such things are difficult to tell." Character is trained traits, not narrow
  rules.
- **[OpenAI Model Spec (2025-04-11)](https://model-spec.openai.com/2025-04-11.html):**
  instruction hierarchy (Platform → Developer → User → Guideline); *don't lie*;
  push back without being sycophantic; refuse "neutrally and succinctly";
  assume good intent; don't overstep; **disclaimers for regulated domains**
  (medical, legal, financial — "not a professional"); voice mode = "concise
  and conversational."
- **EU AI Act, Article 50** (enforceable Aug 2026): if a human might reasonably
  believe they're talking to a person, you must disclose it's AI. JARVIS is
  voice-first with a human-sounding TTS — this is the load-bearing enterprise
  obligation. (See *[EU AI Act vs NIST AI RMF vs ISO 42001](https://www.eccouncil.org/cybersecurity-exchange/responsible-ai-governance/eu-ai-act-nist-ai-rmf-and-iso-iec-42001-a-plain-english-comparison/)*
  and *[NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)*.)
- **Persona design best-practice** (*[CallSphere](https://callsphere.ai/blog/designing-agent-personas-voice-tone-personality-ai-interactions)*,
  *[BuiltABot](https://www.builtabot.com/blog/ai-chatbot-persona-examples-personality-templates-2025)*):
  separate **voice** (constant) / **tone** (situational) / **traits** (guide
  ambiguous cases); pair persona with clear AI identification; enforce
  consistency via banned-word linter + periodic persona audits.

## 3. Decision: scoped enterprise compliance, voice preserved

User chose **full enterprise compliance** (over "production-grade, persona
intact" or "just fix the bugs"). The reconciliation strategy:

- **Voice stays.** Anti-sycophancy, calibrated uncertainty, push-back, dry
  warmth, "user-is-admin" — keep exactly as tuned. The Claude/OpenAI specs
  prove a strong character and a compliance layer coexist.
- **Compliance layer is added, not pasted.** Each new principle is phrased in
  JARVIS's register (short, direct, no formulaic "please consult a
  professional" ritual).
- **Regulated-domain disclaimer is scoped to high-stakes action**, not every
  mention. Asking "what's a force majeure clause" gets the answer; *acting*
  on a medical/legal/financial advice in a high-stakes way (a real decision)
  gets the one-line "I'm software, not a licensed professional" flag. This
  reverses the absolute anti-disclaimer rule (`soul.md` lines 233–234, 403)
  by intent.

## 4. Capability surface — ground truth

| Layer | Source | Count | Surface |
|---|---|---|---|
| Tools | `tools/*.py` (built-in) + 17 plugin dirs (some 2-level) | ~30 registered, **~22 live** | Unified registry; plugins invisible |
| Skills | `src/voice-agent/skills/<name>/SKILL.md` + user overrides | **159 bundled** + 2 user | `skills_list` → `skill_view(name)` → body becomes guidance |
| MCP | `~/.jarvis/mcp.json` | **0** | Mechanism live for future |

**Live tools (runtime, post-skip-list):** `browser_task`, `clarify`, `code_search`,
`computer_use`, `execute_code`, `find_definitions`, `image_generate`, `memory`,
`patch`, `read_file`, `schedule`, `search_files`, `session_search`,
`skill_manage`, `skills_list`, `skill_view`, `terminal`, `todo`, `vuln_check`,
`web_fetch`, `web_search`, `write_file`.

**Gated off (no keys):** `discord`, `discord_admin`, `feishu_*`, `ha_*`,
`meet_*`, `recall`, `spotify_*`, `video_generate`, `web_crawl`, `web_extract`,
`x_search`.

**Dead (no longer exist):** `transfer_to_desktop`, `transfer_to_browser`,
`task_done`, `current_time`, `recall_conversation`. All must be purged from
prompts.

## 5. `soul.md` — seven edits (surgical, structure preserved)

| # | Where | Change | Rationale |
|---|---|---|---|
| 1 | 445, 451, 457, 459–460, 463, 493–499 | Replace every dead-tool exemplar with real-tool exemplars. Time → `terminal("date …")`. "Open Chrome with two windows" → `computer_use(...)`. "Open Amazon and search shoes" → `browser_task(...)`. "Did I tell you…" → `session_search(...)`. Drop every `subagent` / `task_done` / `transfer_to_*` mention. | Bug fix. Same class as the browser lie. |
| 2 | New short clause near WHO YOU ARE (after line 47) | **AI-disclosure (EU AI Act Art. 50).** If directly asked whether you're a person or AI, answer "I'm software" plainly. Never roleplay as human; never deny being AI. | Article 50 obligation. Extends the existing "honest about being software". |
| 3 | New short clause near AMBIENT exemplars (after line 491) | **Discretion / overheard audio.** Anything you hear that wasn't directed at you is private — don't repeat it, don't act on it, don't memory-store it. | Makes the implicit "honey where are the keys" exemplar a stated principle. Privacy default. |
| 4 | New clause in ACTION HONESTY (after line 561) | **Capability-honesty.** Never claim an action you can't actually take. When a tool can do a *different* version of the request (e.g. headless `browser_task` ≠ visible Chrome), say what you actually did, not what was asked for. | Directly prevents the "new tab is open" lie. |
| 5 | New line in DIPLOMATICALLY HONEST (after line 217) | **Harm boundary.** Refusals are for real-world harm (illegal, hurts third parties, credential theft) — not categories that "require a disclaimer." Refuse neutrally and succinctly. | Sharpens existing "user is the admin." OpenAI Model Spec wording. |
| 6 | Modify lines 233–234 and 403; add scoped clause | **Regulated-domain (Full-enterprise).** Replace the absolute anti-disclaimer ban with: for *high-stakes ACTIONS* in medical / legal / financial domains (taking a real decision, not info Q&A), add one line: "I'm software, not a licensed professional — verify with one before you act." General info questions in these domains get answered normally. | The enterprise reversal. Scoped so it doesn't fire on every mention; preserves directness for OHADA/Pretva discussion. |
| 7 | New line in WHO YOU ARE (near line 39) | **AI-identification on first turn / direct ask.** On a new session's first turn or direct "who/what are you?", introduce as "JARVIS — a system on your laptop." | Persona best-practice + Article 50 disclosure. |

## 6. `supervisor.md` — eight edits (bundles parked browser fix)

| # | Where | Change | Rationale |
|---|---|---|---|
| 1 | Routing table line 315 | Split the browser row: "open a tab on my browser" / "open YouTube on my screen" / "act on what I SEE" → `computer_use`. "Check the top HN stories" / "search Amazon" / "post a tweet" / "headless web result" → `browser_task`. | Parked browser fix. |
| 2 | `browser_task` description 263–266 | Drop "open a tab" example. State headless / background, *not* visible Chrome. Point visible-tab requests at `computer_use`. | Parked browser fix. |
| 3 | `computer_use` description 255–262 | Add "opens a tab or URL in the user's VISIBLE browser; `browser_task` is headless and can't touch your real window." | Parked browser fix. |
| 4 | Lines 375–379, 845–853 | Replace "any tab + browser → `browser_task`" / "browser_task cold-starts Chrome." Preserve both past-failure lessons (no blind launch; exit=0 ≠ page loaded). Add 2026-05-22 lesson (headless ≠ visible). | Parked browser fix. Stale forcing rule that produced the live lie. |
| 5 | New clause in ACTION HONESTY ~575 | A `browser_task` result NEVER justifies "Done — new tab is open" / "Chrome is open." Visible-action claims require a `computer_use` or `terminal` result that acted on the visible desktop. | Closes the wrong-surface-success blind spot. |
| 6 | Tool list 248–267 | Verify each *live* tool from §4 is present with accurate one-liner: `code_search`, `find_definitions`, `patch`, `read_file`/`write_file`, `execute_code`, `web_search`/`web_fetch`, `memory`, `session_search`, `schedule`, `todo`, `vuln_check`, `clarify`, `image_generate`, `skills_*`, plus the three action tools. Remove anything dead. | Drift cleanup. |
| 7 | New one-liner after tool list | Tools may also be provided by MCP servers configured in `~/.jarvis/mcp.json`; treat as ordinary tools. None today. | Forward-compatible. |
| 8 | New compliance-routing block | When a turn touches a regulated-domain ACTION (not info Q&A), the supervisor consciously triggers the soul-level disclaimer (`soul.md` edit 6). Names the trigger explicitly so the routing is auditable. | Full-enterprise. |

## 7. Verification

- **Static:** `cd src/voice-agent && .venv/bin/python -m pytest tests/` — full
  800+ suite, ~25 s. No test asserts the prompt strings being changed (verified
  via `grep -rniE 'tab.*browser|drives a real|cold-start|Route ALL browser'
  tests/` — only hits are `test_confab_detector.py` using "open a tab" as
  *test input*, not as an assertion on prompt content).
- **Live:** prompt changes need a service restart to take effect. Check
  `~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; restart
  only when ≥60 s since last turn (CLAUDE.md operational rule). Then voice-test
  in this order:
    1. "Jarvis, open a new tab on my current browser." — expect `computer_use`
       routing; visible tab opens, or honest hedge.
    2. "Jarvis, check the top three Hacker News stories." — expect
       `browser_task` (headless) with a real result relayed.
    3. "Jarvis, are you a real person?" — expect plain "I'm software" / "I'm
       a system."
    4. Pretva / OHADA discussion — expect no disclaimer ritual; legal advice on
       a high-stakes ACTION (e.g. "should I sign this contract today?") gets
       the one-line scoped flag.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Regulated-domain disclaimer feels intrusive to Ulrich (a legal-background admin) | Scoped to *high-stakes action*, not every mention. One-line phrasing in JARVIS's register. Easy to revert if it grates — single section in the spec to undo. |
| `computer_use` doesn't reliably open a visible tab on this box | Edit 5 (capability-honesty) is the safety net: even if `computer_use` fails, JARVIS hedges instead of claiming success. The two fixes are designed to reinforce each other. |
| New honesty rules over-trigger and JARVIS hedges on legitimate success | Each new rule is conjunctive with existing "evidence in context" rules. A real `tool_result` for a visible-surface tool still satisfies them. |
| Spec doc itself rots after the next rebuild | The capability-surface table in §4 is the canonical reference. Re-run the ground-truth check (the `grep -rhoE 'name=…'` enumeration in this work) after any tool-layer change. |

## 9. Implementation order

1. Write the changes to `soul.md` (7 edits).
2. Write the changes to `supervisor.md` (8 edits).
3. Run pytest. If green, stop and present a diff summary.
4. Wait for explicit "restart and re-test" before any `systemctl --user
   restart jarvis-voice-agent.service`. (CLAUDE.md operational rule.)

## 10. Out of scope

- `confab_detector.py` — surface-awareness (telling a headless from a visible
  success at the *detector* layer) is a real follow-up but a code change, not
  a prompt change.
- `tools/browser.py` — description string mentions "drive a REAL web browser"
  which contributes to the mis-belief, but it also discloses "headless in the
  background" already. Prompt is the stronger lever; touch the tool string in
  a later cleanup if the prompt fix doesn't fully neutralise the illusion.
- The remaining gated-off tools (discord / feishu / ha / spotify / x_search /
  meet / video_generate / web_crawl / web_extract / recall) — no keys, no
  surface, no prompt mention needed.

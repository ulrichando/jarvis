# Multi-provider computer use for `/computer-use` — design

**Date:** 2026-06-20
**Status:** design (brainstorm output, pre-plan)
**Goal:** Let the web `/computer-use` feature drive the desktop using **Claude, OpenAI GPT-5.5, and Gemini** — not just Claude — via one provider-agnostic loop + thin per-provider adapters, so adding a provider is one small file. Plus make the feature **always-on** (no manual process starts).

## Verified facts (researched 2026-06-20, NOT assumed)

Model names/capabilities were verified online because training data is stale past Jan 2026 (per the user's "never assume" rule):

- **OpenAI GPT-5.5** (shipped 2026-04-23) — agentic model built for computer use. API id `gpt-5.5` (snapshot `gpt-5.5-2026-04-23`), plus `gpt-5.5-pro`. **Image input + function calling** supported; native computer use via the **Responses API**. Source: developers.openai.com/api/docs/models/gpt-5.5.
- **Google Gemini** — **`gemini-3-flash-preview`** has computer use **built in**; there is also a dedicated **Gemini 2.5 Computer Use** model. Both: vision + function calling. Source: ai.google.dev/gemini-api/docs/computer-use, blog.google.
- **Anthropic Claude** — `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5` (current per environment), custom `computer_use` tool + SOM (already live).
- **SDKs already installed** in `src/voice-agent/.venv`: `anthropic 0.105.2`, `openai 2.38.0`, `google-genai`. **No new deps.**

### Native computer-use status per model (verified 2026-06-20)

Every model we offer is **native computer-use capable** — so there is no "not-native" model in the picker. (We still drive them via the uniform custom-tool + SOM path in v1; native CU is a per-adapter upgrade.) The picker should label each with a small **"native CU"** marker for transparency.

| Model | Native CU? | Notes |
|---|---|---|
| `claude-opus-4-8` | ✓ native | `computer_20251124` |
| `claude-sonnet-4-6` | ✓ native | strongest on OSWorld (~72.7%) |
| `claude-haiku-4-5` | ✓ native | supported, weakest tier |
| `gpt-5.5` / `gpt-5.5-pro` | ✓ native | via Responses API |
| `gemini-3-flash-preview` | ✓ native | computer use **built in** |
| Gemini 2.5 Computer Use | ✓ native | dedicated CU model |

## Decision: uniform custom-tool + SOM path (not native per-provider CU)

All three providers support **vision + function calling**, so each can drive JARVIS's existing **custom `computer_use` tool** (`COMPUTER_USE_SCHEMA`) using **SOM element-mode** (numbered overlays → `element=N`), exactly as Claude does today.

We deliberately do **not** use each provider's *native* computer-use tool in v1, because native CU returns **pixel coordinates at a scaled resolution**, which would force coordinate-scaling and a pixel-click code path that JARVIS's SOM/element approach exists to avoid. SOM is coordinate-free and reuses `handle_computer_use` unchanged. Native CU per provider is a **future per-adapter upgrade** (the adapter interface allows it) — out of scope here.

## Architecture

**Shared, provider-agnostic (already built — unchanged):** the agent loop, the executor `handle_computer_use` (xdotool/mss on `:0`), SOM screenshots (`computer_use_vision`), the sensitive-app blocklist, per-action-type approval, sessions, and the entire web UI (noVNC view, chat, picker, approval cards).

**New — the only per-provider piece:** a `CUAdapter` interface; the loop delegates "given history + the latest screenshot + the `computer_use` tool, what are the next actions?" to it. The **action vocab is identical** across providers (the tool's `action` enum), so `handle_computer_use` is unchanged.

```
CUAdapter (Protocol)
  start(task, system, first_frame)              # seed provider-native conversation state
  async next_step() -> (text|None, [Action])    # call model; parse tool-calls; [] = done
  add_results([ActionResult])                   # feed tool results + fresh frame back

Action       = {"action": str, **args}          # the COMPUTER_USE_SCHEMA vocab
ActionResult = {"text": str, "image_b64": str|None}
```

Implementations (one file each, `src/voice-agent/pipeline/cu_adapters/`):
- `AnthropicCUAdapter` — refactor the existing loop's Anthropic calls behind the interface. `messages` shape, `input_schema` tool, image blocks.
- `OpenAICUAdapter` — `openai` SDK; `computer_use` as a function tool; screenshots as `image_url` content; parse `tool_calls`. Models `gpt-5.5`, `gpt-5.5-pro`.
- `GeminiCUAdapter` — `google-genai` SDK; `computer_use` as a function declaration; screenshots as inline image parts; parse function calls. Models `gemini-3-flash-preview`, `gemini-2.5-computer-use`.

**Routing + gating:** `_provider_for(model_id)` (prefix map: `claude-*`→anthropic, `gpt-*`→openai, `gemini-*`→google) selects the adapter. Each provider is **gated on its key** (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY|GOOGLE_API_KEY`, loaded from `keys.env`); `/health` reports which providers are available so the web picker can dim/disable the rest. `_ALLOWED_MODELS` + the web `CU_MODELS` list grow to include the OpenAI/Gemini ids.

## Data flow

page model picker → model id in `/run` body → web route forwards → sidecar `_provider_for(model)` → adapter → model call → actions → (blocklist → approval → `handle_computer_use` → SOM refresh → screenshot) → `adapter.add_results` → repeat. SSE frames (text/action/permission/blocked/done/error) unchanged.

## Error handling

- Missing provider key → adapter reports unavailable; `/health` marks it; picker disables those models; selecting one returns a clear SSE error.
- Provider/model API error → caught in the loop, emitted as an SSE `error`, session history preserved (image-free).
- Tool-call parse failure (provider returned malformed/again) → treated as "no action," loop asks once more, then stops with an error (bounded by `MAX_STEPS`).

## Operational simplicity (the "simpler to use" win)

Ship the sidecar as a **systemd --user service** (`jarvis-computer-use.service`) so it auto-starts and restarts — no manual `bin/jarvis-computer-use`. The stream (`x11vnc`+`websockify`) gets the same treatment or stays in the launcher. `/health` + the page already report readiness, so "open the page and it works."

## Testing

- Per-adapter unit tests with the SDK **mocked**: assert (a) the `computer_use` tool is formatted correctly for that provider, (b) a screenshot is attached as the right image content type, (c) a provider tool-call response parses into the uniform `Action` vocab, (d) `add_results` feeds text+image back in the right shape.
- Shared-loop tests (already exist for the Anthropic path) run against a fake adapter.
- Provider-gating test: no key → adapter unavailable, `/health` reflects it.
- Reuse-safety: existing `computer_use` pytest stays green (executor unchanged).

## Out of scope (tracked separately)

- Native per-provider CU tools (pixel-coordinate path) — future per-adapter upgrade.
- JARVIS **CLI** computer use (task #50, `src/cli` — needs sign-off).
- The broader CU-docs study (task #49).
- Sandbox/Docker desktop (we drive real `:0`).

## Files

- **New:** `src/voice-agent/pipeline/cu_adapters/{__init__.py,base.py,anthropic.py,openai.py,gemini.py}`; `setup/systemd/jarvis-computer-use.service`.
- **Modify:** `src/voice-agent/computer_use_service.py` (loop delegates to an adapter; `_provider_for`; `_ALLOWED_MODELS` += OpenAI/Gemini; `/health` provider availability); `src/web/.../computer-use/page.tsx` (`CU_MODELS` += OpenAI/Gemini, dim unavailable); `src/web/.../api/computer-use/route.ts` (already forwards `model`).
- **Reuse unchanged:** `tools/computer_use.py` (`handle_computer_use`), `pipeline/computer_use_vision.py`.

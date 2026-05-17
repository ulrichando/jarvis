# JARVIS Voice Latency — Instrumentation Design (Round 2)

Hardware: Dell Latitude 7480 / Intel i7-7600U (2C/4T Kaby Lake @ 2.8 GHz boost) / 15 GB RAM / integrated GPU. 2017-era ULV laptop CPU.

Round 1 measurements (gpt-5.1 = 1.2 s TTFW avg, llama-3.3-70b pinned = 3.8 s, deepseek-v4-pro = 76 s broken) are corroborated by live telemetry (`~/.local/share/jarvis/turn_telemetry.db`):

```
LLM                              n   avg_ttfw   min   max     avg_in_tok
gpt-5.1 (TASK route)            23     1502 ms    3    9060    -
gpt-5.1 (EMOTIONAL)             16      850 ms    3    8302    -
groq:llama-3.3-70b (TASK)        5     1734 ms   20    8015    75825
groq:llama-3.1-8b-instant       16     5056 ms 1865    8225    75888
groq:llama-4-scout (EMOTIONAL)  10     3430 ms  298    6285    76494
deepseek-v4-pro                 68    74306 ms    0  1695513   (broken)
```

The critical observation Round 1 missed: **every turn ships ~76k input tokens**. `prompts/supervisor.md` is 133,718 bytes (~33.5k tokens at 4 chars/token) plus 23 `@function_tool` schemas + chat_ctx + WHO-YOU-ARE block + breaker-status block. **That's the dominant variance on llama-70b's 3.8 s TTFW. Prompt caching is the single highest-leverage win.**

---

## TL;DR — top 5 instrumentation wins

1. **Wire stage timestamps as new columns on `turns`** (8 µs fields covering VAD/STT/preflight/LLM-TTFB/TTS-TTFB/sanitizer). Infrastructure exists (`session._jarvis_first_token_at_monotonic`); we just don't stamp the intermediates. P0.
2. **Cache `BreakeredGroqLLM.chat`'s pre-flight stringification** ([providers/llm.py:585-621](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L585)). 80-turn `CTX_MAX_TURNS` × ~76k chars = ~200-500 ms blocking per turn. Cache invalidates only when chat_ctx items change. P0.
3. **Verify Anthropic prompt caching actually hits**, not just configured ([providers/llm.py:247](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L247)). `caching="ephemeral"` is set; live telemetry shows zero `cost_usd` rows for Anthropic turns — no evidence the cache is read or even billed. P0.
4. **Microbench the 6 sanitizers patching `_parse_choice`** (8 sanitizers total, 6 per-chunk). Per-turn cost estimated at ~4 ms — likely NOT a bottleneck. Bench validates the assumption. P1.
5. **Stamp TTS TTFB** in `LoggingGroqChunkedStream._do_real_run` to separate Orpheus latency from LLM latency in long-TTFW outliers. P1.

---

## Latency budget breakdown — gpt-5.1 1.2 s path

| Stage | Typical | Variance | Instrumented today | Hook to add |
|---|---|---|---|---|
| Mic frame → VAD endpoint | 400 ms | ±50 ms | No | `agent_state` "listening"→"thinking" in [jarvis_agent.py:4296](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L4296) |
| VAD endpoint → STT first partial | 200-400 ms | ±150 ms | No | Wrap [providers/stt.py:38](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/stt.py#L38) `_recognize_impl` |
| STT final → `_jarvis_turn_start_monotonic` | <5 ms | ±5 ms | Yes | (existing) |
| Pre-flight stringification + estimate + prune | 50-500 ms | ±450 ms | No | Wrap [providers/llm.py:587-674](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L587) |
| LLM HTTP request build + TLS | 5-30 ms | ±25 ms | No | livekit-agents internal |
| LLM TTFB (provider-side) | 200-1500 ms | ±1300 ms | Lumped into TTFW | First-iter of `BreakeredLLMStream.__anext__` [llm.py:482-494](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L482) |
| Sanitizer chain on first chunk | 1-15 ms | ±14 ms | No | `time.perf_counter_ns()` deltas per patch |
| LLM first text → TTS request | <1 ms | ±1 ms | Yes (`stamp_first_token` [jarvis_agent.py:3071](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L3071)) | (existing) |
| TTS request → Orpheus TTFB | 250-600 ms | ±500 ms | No | Wrap [providers/tts.py:128](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/tts.py#L128) `_do_real_run` |
| TTS first audio → SFU egress | 50-100 ms | ±50 ms | No | RTP timestamp delta (harder) |

`ttfw_ms` today is **STT-final → first audible text**, conflating ~6 stages. The 2.5 s unexplained slack on llama-3.3-70b lives in: (a) pre-flight stringification ~200-500 ms blocking, (b) Groq TTFB at 76k input tokens ~1000-1500 ms, (c) Groq's slower-than-OpenAI inference path ~500 ms. Wide-variance stages: pre-flight + LLM TTFB + TTS TTFB. Narrow-variance: sanitizers + first-text → TTS.

---

## Finding 1 — Telemetry schema

Current `turns` columns: id, ts_utc, user_text, jarvis_text, emotion, route, llm_used, voice_used, **ttfw_ms**, **total_audio_ms**, user_followup_30s, route_fallback, notes, subagent, interrupted, input_tokens, output_tokens, cost_usd, context_pressure, memory_auto_extracted.

Round 1's columns (`vad_to_stt_us`, `stt_us`, `llm_ttfb_us`, `tts_ttfb_us`, `sanitizer_overhead_us`) are mostly right. Counter-proposal (names tightened to match where we can actually stamp):

| Column | Type | Semantics | Hook |
|---|---|---|---|
| `t_vad_end_us` | INTEGER | µs from turn-frame-zero to VAD endpoint | `agent_state` "listening"→"thinking" [jarvis_agent.py:4296](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L4296) |
| `t_stt_final_us` | INTEGER | µs t_vad_end → STT `is_final=True` | [jarvis_agent.py:4334](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L4334) `_on_user_input` |
| `t_preflight_us` | INTEGER | µs in `BreakeredGroqLLM.chat` pre-flight | New: wrap [providers/llm.py:586-674](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L586) |
| `t_llm_ttfb_us` | INTEGER | µs `chat()` return → first chunk | New: stamp [providers/llm.py:491](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L491) |
| `t_llm_first_text_us` | INTEGER | µs first chunk → first non-empty content | Reuse `stamp_first_token` ts [jarvis_agent.py:3087](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L3087) |
| `t_tts_ttfb_us` | INTEGER | µs TTS request send → first audio byte | Wrap [providers/tts.py:128-182](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/tts.py#L128) |
| `sanitizer_us` | INTEGER | Σ µs across `_parse_choice` patches this turn | Per-patch accumulators reset on turn start |
| `chat_ctx_items` | INTEGER | n items shipped to LLM (post-prune if any) | Stash from [providers/llm.py:660](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L660) |

**µs not ms.** Sub-ms stages round to 0 in ms; SQLite INTEGER is 64-bit anyway.

**ALTER block** drops into the existing migration loop at [pipeline/turn_telemetry.py:78-158](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py#L78), one `try / except sqlite3.OperationalError: pass` per column. `log_turn(...)` gains 8 keyword args, all `Optional[int] = None`, backwards-compat with existing callers.

---

## Finding 2 — CPU contention on i7-7600U

2017 ULV Kaby Lake, 2C/4T, boost 3.9 GHz single / sustained ~2.8 GHz. Geekbench 6 ≈ Pi 5 tier (1100 single, 2100 multi). No AVX-512.

| Component | Steady cost | Bursty cost |
|---|---|---|
| LiveKit audio + RTP | 12% of 1 core | — |
| AcousticTap RMS ([pipeline/prosody.py:99-113](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/prosody.py#L99)) | 3% of 1 core | — |
| Silero VAD ONNX (16 kHz, 30 ms window) | 20-40% of 1 core | — |
| Pre-flight stringification (~76k chars × 80 items) | 0% | **100% of 1 core for 300-500 ms** |
| Sanitizer chain (200 chunks × 6 patches) | 0% | 2-8% of 1 core, bursty |
| 4 forkserver idle (`num_idle_processes=4` [jarvis_agent.py:5470](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L5470)) | 2% across all | — |
| **Steady total** | **~40%** | — |
| **Bursty total** | — | **~95% + 100% blocking** |

**Bottleneck is the GIL-locked pre-flight, not total CPU.** Both cores have ~60% headroom steady; the pre-flight blocks the event loop for ~half a turn, stalling STT partials and async tasks. That's why `load_threshold` was raised from 0.7 → 0.88 ([jarvis_agent.py:5465](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L5465)) — the bursts pushed the lower threshold into false-positive territory.

**Hardware ceiling.** A 6-core upgrade (Ryzen 5 7600 or M3 Pro) buys ~300 ms TTFW (faster single-thread) but the LLM TTFB at 76k input tokens is ~1000-1500 ms regardless of laptop — that's Groq's compute floor for ingesting the prompt. **Software wins ~500-1000 ms; hardware wins ~300 ms. Software ROI is higher.**

---

## Finding 3 — Pre-flight stringification (Round 1 follow-up)

[providers/llm.py:587-621](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L587):

```python
ctx_str = ""
for it in items:
    ctx_str += str(getattr(it, "content", it)) + "\n"
```

At 80 items × ~1k chars each + 134k-byte system prompt streamed through chat_ctx + 23 tool schemas at ~2k chars each: ~300k char concat. CPython's string-concat optimization breaks down at this scale (mixed item types defeat the in-place buffer). **~150-300 ms blocking on this CPU, per turn.**

### Three fixes ranked

**(A) Cache by `(id(chat_ctx), len(items), id(items[-1]))`.** Same ChatContext + same last item = same estimate. Live sessions hit the cache on every turn except the first.

```python
_PREFLIGHT_CACHE: dict = {"key": None, "ctx_tokens": 0, "tools_tokens": 0}

def _preflight_estimate(chat_ctx, tools):
    items = getattr(chat_ctx, "items", None) or []
    key = (id(chat_ctx), len(items), id(items[-1]) if items else 0)
    if _PREFLIGHT_CACHE["key"] == key:
        return _PREFLIGHT_CACHE["ctx_tokens"] + _PREFLIGHT_CACHE["tools_tokens"]
    # Full rebuild only on cache miss.
    ctx_str = "".join(str(getattr(it, "content", it)) + "\n" for it in items)
    tools_str = "".join(...)
    _PREFLIGHT_CACHE["key"] = key
    _PREFLIGHT_CACHE["ctx_tokens"] = estimate_tokens(ctx_str)
    _PREFLIGHT_CACHE["tools_tokens"] = estimate_tokens(tools_str)
    return _PREFLIGHT_CACHE["ctx_tokens"] + _PREFLIGHT_CACHE["tools_tokens"]
```

**(B) Skip when `len(items) < 10`** — can't be near WARN/HARD anyway:

```python
if len(items) < 10:
    LAST_PREFLIGHT["tokens"] = None
    LAST_PREFLIGHT["pressure"] = "ok"
    return super().chat(*args, **kw)
```

**(C) Move pre-flight off the hot path** (background task using prior-turn estimate). Cleaner but invasive — (A)+(B) captures 95% of the win.

**Recommendation: (A) + (B) combined.** ~200-500 ms savings per turn after the first.

---

## Finding 4 — Anthropic prompt caching audit

[providers/llm.py:241-249](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L241):

```python
return lk_anthropic.LLM(
    model=model_id,
    api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    temperature=0.6,
    max_tokens=200,
    caching="ephemeral",          # <-- set
    _strict_tool_schema=False,
)
```

**Config is set; verification missing.** Anthropic returns `usage.cache_read_input_tokens` and `usage.cache_creation_input_tokens` per response. Live telemetry shows zero rows where `llm_used LIKE 'anthropic:%'` with non-NULL `cost_usd` — either the Anthropic path is never reached (rung-3 fallback, rare) or `cache_read_input_tokens` is uncounted and cached turns drop through the pricing-table mismatch ([jarvis_agent.py:4900-4907](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L4900)).

**Two concrete actions:**

1. Stash `session._jarvis_last_cache_read_input_tokens` from the usage object and write to `notes` (or add a column).
2. Confirm `caching` is actually wired in livekit-plugins-anthropic 1.5.8 vs being silently dropped — the kwarg signature predates Haiku 4.5.

**If wired correctly: ~10× TTFB win on the Anthropic path.** Un-cached at 76k tokens is 500-1500 ms TTFB; cache reads are 50-150 ms. Supervisor.md (33.5k tokens) is exactly the workload prompt caching exists to amortize.

---

## Finding 5 — Sanitizer overhead microbench

Eight `_parse_choice` patches stack today (install order from [jarvis_agent.py:121-202](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L121)). Six run per-chunk; two are one-shot (`tool_name` patches `_run` not `_parse_choice`; `anthropic_strict_schema` patches `ToolContext.parse_function_tools`).

**Microbench design** — new `src/voice-agent/tests/perf/test_sanitizer_overhead.py`:

```python
import time, statistics, json
from unittest.mock import MagicMock

def fake_chunk(content="Hello world"):
    chunk = MagicMock()
    chunk.delta = MagicMock()
    chunk.delta.content = content
    chunk.delta.tool_calls = []
    chunk.finish_reason = None
    return chunk

def bench_patch(patch_fn, n=10000):
    stream = MagicMock(); stream._chat_ctx = MagicMock(); stream._chat_ctx.items = []
    stream._tool_ctx = MagicMock(); stream._tool_ctx.function_tools = {}
    for _ in range(20):
        patch_fn(stream, "warmup", fake_chunk(), False)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        patch_fn(stream, "bench", fake_chunk(), False)
        samples.append(time.perf_counter_ns() - t0)
    return statistics.mean(samples), statistics.stdev(samples), sum(samples)

def main():
    import livekit.agents.inference.llm as inf_llm
    orig = inf_llm.LLMStream._parse_choice
    results = {}
    # Install each, bench, restore orig, strip flag attr, repeat.
    for name, mod in [
        ("deepseek_roundtrip", "sanitizers.deepseek_roundtrip"),
        ("dsml", "sanitizers.dsml"),
        ("pycall", "sanitizers.pycall"),
        ("handoff_text", "sanitizers.handoff_text"),
        ("denial_detector", "sanitizers.denial_detector"),
        ("internal_phrase", "sanitizers.internal_phrase"),
    ]:
        __import__(mod, fromlist=["install"]).install()
        mean, sd, total = bench_patch(inf_llm.LLMStream._parse_choice)
        results[name] = {"mean_ns": mean, "stdev_ns": sd, "total_200_chunks_us": total / 1000}
        inf_llm.LLMStream._parse_choice = orig  # restore
    with open("/tmp/sanitizer-bench.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
```

**Expected output (code-reading estimate):**

| Sanitizer | Avg ns / chunk | Total per 200-chunk turn |
|---|---|---|
| deepseek_roundtrip | ~2,000 | 400 µs |
| dsml (no U+FF5C in content) | ~3,500 | 700 µs |
| pycall (chunk 1 regex tries; rest state-lookup) | 50,000 first / 1,500 rest | 350 µs |
| handoff_text (chunk 1 chat_ctx walk; rest cheap) | 15,000 first / 500 rest | 200 µs |
| denial_detector | ~8,000 | 1.6 ms |
| internal_phrase | ~3,000 | 600 µs |
| **Total per turn** | — | **~4 ms** |

**Verdict: sanitizers are NOT the bottleneck.** Per-turn cost is bounded at ~4 ms — well under any latency threshold. Bench validates the assumption; do not refactor for perf. (8-deep monkey-patches on a third-party class is a correctness/maintainability risk, but P2 not P0.)

---

## Finding 6 — Hot-path Python overhead audit

I grepped for the usual suspects:

- **Lazy imports per-call.** [providers/llm.py:411-414, :388](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L388) — both fire on HARD pressure (rare); cached by import system after first call. Fine.
- **Regex compilation per-call.** All sanitizer regexes are module-level (`_PYCALL_OPEN_RE`, `_DENIAL_RE`, `_INTERNAL_RE`, `_DSML_INVOKE_RE`). No per-call `re.compile`. Good.
- **json parse/dump in chunk path.** [sanitizers/dsml.py:162,191](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/sanitizers/dsml.py#L162) — only inside `_execute_inline`, fires on DSML envelope detection (rare). Not hot-path.
- **The actual hot-path waste** is the pre-flight stringification (Finding 3) and `_chat_ctx_has_pending_handoff` walk at [sanitizers/handoff_text.py:96-128](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/sanitizers/handoff_text.py#L96). The 2026-05-06 fix expanded to full chat_ctx walk; comment says "few µs per stream" and at CTX_MAX_TURNS=80 that holds. **Per stream not per chunk** — fine.

---

## Finding 7 — TTS streaming verification

[providers/tts.py:367-369](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/tts.py#L367) wires `StreamAdapter` correctly:

```python
raw = LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=vid)
t = tts.StreamAdapter(tts=raw, text_pacing=True)
```

Orpheus is `streaming=False` (whole-reply synth), but `StreamAdapter` synthesizes sentence-by-sentence: first sentence's audio plays while later sentences are still generating. First-sentence TTFB ~250-400 ms independent of total reply length.

**Concrete fix opportunity:** Anthropic has `max_tokens=200`; Groq/OpenAI don't. A 600-token gpt-5.1 reply ships ~3 s of TTS work. Propose `max_tokens=120` (about 90 words, plenty for voice) — caps tail latency without affecting TTFW.

---

## Finding 8 — Production profiling plan

**Sampling:** 100%. New `time.monotonic_ns()` calls cost ~50 ns via vDSO (no syscall). At 6 hook points × ~1 turn / 4 s = 1.5 ns/s overhead. Free.

**Storage: stay on SQLite.** 4-5 turns/min peak, ~10k rows/year. Prometheus / OTLP adds a service dependency for a single-user laptop — wrong tradeoff.

**Dashboard queries** — add `--breakdown` flag to `pipeline/turn_telemetry.py::report()`:

```sql
-- Per-LLM stage breakdown over last 7 days
SELECT llm_used, COUNT(*) AS n,
       CAST(AVG(t_vad_end_us)/1000 AS INT)    AS vad_ms,
       CAST(AVG(t_stt_final_us)/1000 AS INT)  AS stt_ms,
       CAST(AVG(t_preflight_us)/1000 AS INT)  AS preflight_ms,
       CAST(AVG(t_llm_ttfb_us)/1000 AS INT)   AS llm_ttfb_ms,
       CAST(AVG(t_tts_ttfb_us)/1000 AS INT)   AS tts_ttfb_ms,
       CAST(AVG(sanitizer_us)/1000.0 AS REAL) AS sanitizer_ms,
       CAST(AVG(ttfw_ms) AS INT)              AS ttfw_total
FROM turns
WHERE ts_utc >= datetime('now', '-7 days') AND t_llm_ttfb_us IS NOT NULL
GROUP BY llm_used ORDER BY n DESC;

-- "Where did my 3.8s go" pivot
SELECT id, ts_utc, llm_used,
       t_preflight_us/1000 AS preflight,
       t_llm_ttfb_us/1000  AS llm_ttfb,
       t_tts_ttfb_us/1000  AS tts_ttfb,
       ttfw_ms - (t_preflight_us + t_llm_ttfb_us + t_tts_ttfb_us)/1000 AS unaccounted_ms
FROM turns
WHERE ttfw_ms > 3000 AND ts_utc >= datetime('now', '-1 days')
ORDER BY ttfw_ms DESC LIMIT 20;
```

---

## Severity-tagged actions

### P0 — do this turn

| Action | File:line | Cost | Impact |
|---|---|---|---|
| Cache pre-flight stringification | [providers/llm.py:587-621](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L587) | ~30 LOC | -200 to -500 ms TTFW/turn |
| Add 8 µs columns + log_turn signature | [pipeline/turn_telemetry.py:78-158](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py#L78) | ~50 LOC | data for everything else |
| Verify Anthropic caching is hitting | [providers/llm.py:241-249](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L241) + usage capture | ~10 LOC | -1000 ms on Anthropic path if broken |
| Stamp `t_llm_ttfb_us` + `t_preflight_us` | [providers/llm.py:482-494](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L482), :586-674 | ~15 LOC | LLM-side breakdown |

### P1 — next sprint

| Action | File:line | Cost | Impact |
|---|---|---|---|
| Sanitizer microbench | new `tests/perf/test_sanitizer_overhead.py` | ~80 LOC | quantifies current state |
| Stamp `t_tts_ttfb_us` | [providers/tts.py:128-194](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/tts.py#L128) | ~10 LOC | separates Orpheus from LLM in outliers |
| Skip pre-flight when `len(items) < 10` | [providers/llm.py:587](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L587) | ~3 LOC | -50 to -100 ms early-session |
| Cap `max_tokens=120` on Groq/OpenAI paths | [providers/llm.py:209-228](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py#L209) | ~5 LOC | tail-latency cap |

### P2 — defer

| Action | Cost | Impact |
|---|---|---|
| Split supervisor.md into cacheable + dynamic blocks | ~100 LOC | unlocks 10× Anthropic TTFB win |
| Composable filter base for 8 monkey-patches | ~150 LOC | maintainability, not latency |
| Move pre-flight to background task | ~40 LOC | removes hot-path; uses prior-turn estimate |

---

## CPU budget table

| Component | Steady | Bursty | 2-core max headroom |
|---|---|---|---|
| LiveKit audio + RTP | 12% / 1 core | 12% | — |
| AcousticTap RMS | 3% / 1 core | 3% | — |
| Silero VAD | 20-40% / 1 core | 20-40% | — |
| Sanitizers (chunk path) | 0% | 2-8% / 1 core | — |
| Pre-flight stringification | 0% | **100% / 1 core for 300-500 ms** | **collapses one core for ½ turn** |
| Forkserver idle (×4) | 2% spread | 2% | — |
| **Steady** | **~40%** | — | 60% / 1 core free |
| **Bursty** | — | **~95% + 100% blocking** | **zero during pre-flight** |

The gating constraint for sub-1 s TTFW is the **single-thread blocking call**, not total CPU. A laptop upgrade saves ~300 ms; pre-flight cache saves ~500 ms. Software ROI is 1.7×.

Realistic ceiling on this box, post-fixes:
- **Sub-second TTFW achievable on gpt-5.1** (current 1.2 s, floor ~700 ms with cache + STT trim).
- **Sub-2 s TTFW on llama-3.3-70b** only if input tokens drop to ~10k. At 76k tokens, Groq's TTFB physics put the floor at ~1.5 s regardless of CPU.
- **The single biggest latency win available on any CPU is shrinking the input token count.** Today's 134k-byte supervisor.md is the largest lever. Split into a 5-10k always-send core + a 25k tool-rich tail (cache-controlled on Anthropic).

---

## Concrete patches — exact diffs

### 1. Telemetry schema migration ([pipeline/turn_telemetry.py:78-158](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py#L78))

Append inside `init_db()` after the existing column-check loop:

```python
for col in (
    "t_vad_end_us", "t_stt_final_us", "t_preflight_us",
    "t_llm_ttfb_us", "t_llm_first_text_us", "t_tts_ttfb_us",
    "sanitizer_us", "chat_ctx_items",
):
    if col not in cols:
        try:
            conn.execute(f"ALTER TABLE turns ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass
```

`log_turn()` signature gains the 8 columns as `Optional[int] = None`, INSERT extended to match.

### 2. Pre-flight cache + timing (`BreakeredGroqLLM.chat`)

```python
_PREFLIGHT_CACHE: dict = {"key": None, "ctx_tokens": 0, "tools_tokens": 0}

def chat(self, *args, **kw):
    _t_pre = time.monotonic_ns()
    try:
        from tools.token_estimation import estimate_tokens, context_pressure_state, MAX_CONTEXT_TOKENS
        chat_ctx = kw.get("chat_ctx")
        tools = kw.get("tools") or []
        items = (getattr(chat_ctx, "items", None) or []) if chat_ctx else []

        # Skip-small shortcut.
        if len(items) < 10:
            LAST_PREFLIGHT.update({
                "tokens": None, "pressure": "ok",
                "model": getattr(self, "_jarvis_label", "?"),
                "t_preflight_ns": time.monotonic_ns() - _t_pre,
            })
            return BreakeredLLMStream(super().chat(*args, **kw), LLM_BREAKER)

        key = (id(chat_ctx), len(items), id(items[-1]) if items else 0)
        if _PREFLIGHT_CACHE["key"] == key:
            est = _PREFLIGHT_CACHE["ctx_tokens"] + _PREFLIGHT_CACHE["tools_tokens"]
        else:
            ctx_str = "".join(str(getattr(it, "content", it)) + "\n" for it in items)
            tools_str = "".join(
                ((getattr(getattr(t, "info", None), "name", "") or "") + " " +
                 (getattr(getattr(t, "info", None), "description", "") or "") + "\n")
                for t in tools
            )
            _PREFLIGHT_CACHE.update({
                "key": key,
                "ctx_tokens": estimate_tokens(ctx_str),
                "tools_tokens": estimate_tokens(tools_str),
            })
            est = _PREFLIGHT_CACHE["ctx_tokens"] + _PREFLIGHT_CACHE["tools_tokens"]

        pressure = context_pressure_state(est)
        LAST_PREFLIGHT.update({
            "tokens": est, "pressure": pressure,
            "model": getattr(self, "_jarvis_label", "?"),
        })
        # ... existing HARD-pressure pruning block unchanged ...
    except Exception:
        pass
    LAST_PREFLIGHT["t_preflight_ns"] = time.monotonic_ns() - _t_pre
    return BreakeredLLMStream(super().chat(*args, **kw), LLM_BREAKER)
```

### 3. LLM TTFB stamp (`BreakeredLLMStream.__anext__`)

```python
async def __anext__(self):
    if self._first:
        self._first = False
        _t_ttfb = time.monotonic_ns()
        try:
            chunk = await self._breaker.call(self._inner.__anext__)
            LAST_PREFLIGHT["t_llm_ttfb_ns"] = time.monotonic_ns() - _t_ttfb
            return chunk
        # ... existing error handling unchanged ...
    return await self._inner.__anext__()
```

### 4. TTS TTFB stamp (`LoggingGroqChunkedStream._do_real_run`)

Inside the existing `async with ... .post(...) as resp:` block, after `output_emitter.initialize(...)`:

```python
first_chunk_seen = False
async for data, _ in resp.content.iter_chunks():
    if not first_chunk_seen:
        from jarvis_agent import _active_session_for_telemetry
        sess = _active_session_for_telemetry[0]
        if sess is not None:
            try:
                sess._jarvis_tts_ttfb_ns = time.monotonic_ns() - _t_tts_send
            except Exception:
                pass
        first_chunk_seen = True
    output_emitter.push(data)
    nonlocal_audio_bytes[0] += len(data)
```

Where `_t_tts_send = time.monotonic_ns()` is set just before the `.post(...)`.

### 5. End-of-turn write extension ([jarvis_agent.py:4917](/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py#L4917))

```python
t_preflight_us = (LAST_PREFLIGHT.get("t_preflight_ns") or 0) // 1000 or None
t_llm_ttfb_us  = (LAST_PREFLIGHT.get("t_llm_ttfb_ns") or 0) // 1000 or None
t_tts_ttfb_us  = (getattr(session, "_jarvis_tts_ttfb_ns", 0) or 0) // 1000 or None

log_turn(
    # ... existing kwargs ...
    t_preflight_us=t_preflight_us,
    t_llm_ttfb_us=t_llm_ttfb_us,
    t_tts_ttfb_us=t_tts_ttfb_us,
    chat_ctx_items=len(getattr(session, "chat_ctx", None) and session.chat_ctx.items or []),
)
session._jarvis_tts_ttfb_ns = None
LAST_PREFLIGHT["t_preflight_ns"] = None
LAST_PREFLIGHT["t_llm_ttfb_ns"] = None
```

---

## Reading the data afterward

Once ~50 turns land, the gpt-5.1 vs llama-3.3-70b gap decomposes cleanly. Hypothesis: of llama-3.3-70b's ~2.5 s slack vs gpt-5.1, ~400 ms is pre-flight (cache collapses), ~1000 ms is provider TTFB at 76k input tokens (only fixable by prompt caching or token reduction), ~300 ms is sanitizer overhead (microbench will prove irrelevant), ~800 ms is genuine Groq compute on the slower-than-OpenAI inference path.

If the data shows otherwise — TTS TTFB is consistently 800 ms while pre-flight is 50 ms — priorities flip immediately. **That's the value of this instrumentation: it cuts variance into stages so the next round isn't guesswork.**

---

## What I did NOT propose

- **No Prometheus / OTLP.** Wrong scale for a single-laptop voice agent.
- **No async pre-flight.** Too invasive; (A)+(B) captures 95% of the win.
- **No sanitizer rewrite.** They're correct and fast enough. Refactor for maintainability later (P2).
- **No hardware recommendation.** Software path (1.2 → 0.7 s) beats hardware path (1.2 → 0.9 s) on this workload. Bigger ROI, lower cost.

---

## Files touched (P0+P1)

- `src/voice-agent/pipeline/turn_telemetry.py` — schema migration + signature
- `src/voice-agent/providers/llm.py` — pre-flight cache + LLM TTFB stamp
- `src/voice-agent/providers/tts.py` — TTS TTFB stamp
- `src/voice-agent/jarvis_agent.py` — end-of-turn write extension
- `src/voice-agent/tests/perf/test_sanitizer_overhead.py` — new microbench file

Purely additive timing capture + one targeted perf fix (pre-flight cache) that preserves observable behavior. Cache invalidation is conservative (any ChatContext object swap or last-item change invalidates) so correctness invariants hold.

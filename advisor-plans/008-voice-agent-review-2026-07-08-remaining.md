# 007 — Voice-agent general review 2026-07-08: remaining findings

Follow-up backlog from the 2026-07-08 whole-`src/voice-agent` review (22 parallel
finders + adversarial verification). The verification phase hit a session limit
partway through, so most findings below were **never actually refuted — their
verifier agents crashed**. Treat every item here as **UNVERIFIED**: read the
cited code and confirm the failure path before changing anything. Several are
likely false positives.

## Already fixed (this branch, `voice-agent-review-2026-07-08`)

All 11 workflow-*confirmed* findings + all 13 HIGH-severity findings + a batch of
clearly-real stability MEDIUMs — 8 commits, +17 regression tests, full suite
green (3418 passed). See git log for the exact set. Highlights: SSRF (mapped-v6 +
redirect), file-tool write/read denylist + V4A silent-corruption, frontmatter
injection (skills + agents), `on_user_turn_completed` NameError, procedure-capture
counter, DTLN FIR + pw-dump off the realtime mic path, restored token-preflight
prune, OpenAI CU-adapter empty-`tool_calls` 400, schedule `confirm` action, ACP
`._func` dispatch + `keys.env` loading, re-homed `speaking_tracker` feed,
negation regex, two sanitizer-state leaks, config import-crash, session_search
connection leak, tolerant cost-pricing lookup.

## MEDIUM — verify then fix (highest value first)

Each: confirm the path is live + reachable, then fix. Prefer `asyncio.to_thread`
for blocking I/O on the event loop; a strong ref (`self._bg_tasks.add(t)`) for
fire-and-forget `create_task`.

- **pipeline/turn_telemetry.py:602** — `log_turn` does blocking sqlite (+ a
  retention-prune VACUUM) on the event loop; can stall the voice loop up to the
  5 s busy-timeout. Confirm it runs on the loop (not already offloaded), then
  `to_thread` the write.
- **pipeline/conversation_store.py:181** — per-turn sqlite writes synchronous on
  the event loop, default 5 s busy timeout. Same fix shape.
- **providers/gemini_cache.py:255** — blocking `caches.create/delete` (no
  timeout) inside `LLM.chat()` on the event loop. Only live when a Gemini route
  is active. Add timeouts + offload.
- **tools/terminal_tool.py:282** — foreground timeout kills only the shell;
  pipeline/compound children survive as orphans. Kill the process group.
- **tools/terminal_tool.py:385** — background-process registry grows unbounded,
  never reaps; `_poll_bg_process` has no callers. Reap on completion / cap size.
- **tools/dispatch_agent.py:361** — background dispatch task via
  `asyncio.create_task` with no strong ref (GC risk mid-run).
- **tools/command_safety.py:176** — secret-exfil check ignores its own documented
  command segmentation; blocks independent `.env` + `curl` segments (false
  positive that annoys, not a security hole).
- **plugins/memory/honcho/__init__.py:115** — client + handles cached across
  different asyncio event loops → silent recall/sync failures. Key the cache by
  running loop, or rebuild on loop change.
- **vision/person_tracker.py:240** — `stop()` is a no-op (no stop flag); a later
  `start()` spawns a second capture loop. **:196** — tracker JPEG written
  non-atomically (torn-read race with webcam.py). Atomic temp+rename.
- **pipeline/screen_share_observer.py:547** / **screen_share_sink.py:93** —
  GoAway clean-reconnect path can't fire (FIRST_EXCEPTION vs a returning drain);
  sink overwrites the prior consume task without cancelling it.
- **pipeline/bargein_tap.py:226** — partial barge-in checks `in_cooldown()`
  before kill-phrases, so "stop"/"wait" are suppressed during post-barge-in
  cooldown (violates echo_gate's documented invariant). **:258** — Vosk
  Accept/PartialResult run synchronously on the event loop.
- **resilience/llm_idle_timeout.py:62** — the "idle" timeout is actually a
  whole-stream deadline; healthy >30 s streams get killed + re-rolled. Confirm
  intent vs CLAUDE.md before changing (may be deliberate).
- **pipeline/prompt_builder.py:372** — procedure fuzzy match: any common 3-letter
  word matches 4+ char name chunks at Levenshtein ≤ 3 (over-matching).
- **tools/discord.py:63** — LLM-supplied Discord IDs interpolated into API URL
  paths without validation (path injection; env-gated tool).
- **plugins/spotify/client.py:620/600** — `normalize_spotify_uri` returns the raw
  URL when no `expected_type`; breaks on `intl-xx/` locale-prefixed URLs.
- **pipeline/memory_provider.py:125**, **sanitizers/dsml.py:263**,
  **jarvis_agent.py:7655** — fire-and-forget `create_task` with no strong ref.
- **sanitizers/*** buffer cleanup (denial_detector:150, output_language) — per-
  stream buffers only cleaned on finish_reason; barge-in-cancelled streams leak.
- **animators/blender_face.py:372** — change-gated shapes-file writes collide
  with the frame server's 0.5 s staleness check (face snaps shut in degraded /
  test modes). Only relevant if the Blender face is in use.
- **voice_client_watchdog.py:175** — restarts on an instantaneous `agent_present`
  dip, not a sustained absence (spurious restarts). **voice_client_http_api.py:221**
  — blocking `subprocess.run(systemctl)` on the /mute hot path; **:81** — blocking
  urllib to Ollama in an async handler.
- **tools/token_estimation.py:126** — `cache_read_rate`/`_CACHE_READ_DISCOUNT`
  are dead: the documented cache discount is never applied (overstates cached-
  token cost). Wire it into `log_turn`'s cost math or drop it.

## LOW — dead code / cleanup (grep the WHOLE repo incl. bin/, setup/, scripts/,
## desktop-tauri/src-tauri/, src/cli/src/bridge/, tests/ before deleting)

- Orphan modules/functions flagged dead: `reconnect_control.py`, `evolution/`
  package, `tools/ax_tree.py` (docstring-only sketch), `pipeline/cron_scheduler.py`
  `run_forever`/`set_live_say`, `pipeline/dep_check_reader.py` voice-digest half,
  `pipeline/hooks.py` `fire_hook_sync`, `confab_detector.py` `looks_like_confabulation`
  + save-claim machinery, `plugins/*/emergency_cleanup`, `providers/llm.py`
  `BreakeredLLMStream`/`LLM_KWARGS` (no constructor since Groq purge),
  `pipeline/specialty_routes.py` `routes_with_retry_chain`, `pipeline/turn_graph.py`
  BANTER fast-path nodes, `voice_client_screen_share.py` `_FRAME_BYTES`.
- Stale docstrings/env refs: `JARVIS_SCREEN_SHARE_FFMPEG` (unread), Orpheus log
  markers in `animators/blender_face.py:200`, `providers/tts.py` default-voice
  mismatch (GuyNeural vs ChristopherNeural), `pipeline/config.py` documents
  removed subsystems.
- Duplication: `_direct_unit_live`/`_DIRECT_UNITS` copied in voice_client_http_api
  + jarvis_agent (already diverging); `META_SILENCE_RE` drifted copy in chat_ctx
  vs pycall; FACECAP_ALIASES vs viseme_tables conflicting maps.
- CORS: `voice_client_http_api.py:651` hand-set `Access-Control-Allow-Origin: *`
  headers are inert (overwritten by `_origin_guard`).

## Method note

Filesystem/live-DB is ground truth. Two findings in this review were live-
confirmed by querying `~/.local/share/jarvis/turn_telemetry.db` (the cost-NULL
labels) and by numeric scipy comparison (the DTLN FIR taps). Do the same here —
don't trust a finder's severity or a stale docstring.

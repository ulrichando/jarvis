# JARVIS environment-variable reference

This document is the complete manifest of environment variables read by the
JARVIS voice agent (`src/voice-agent/`). It also lists the required provider
keys shared across subtrees.

**208 `JARVIS_*` flags** were captured by grepping `src/voice-agent/` at
commit time (2026-05-29). The overwhelming majority are **optional
kill-switches or feature-gates with safe defaults** — you do not need to set
them to run JARVIS. The required keys are listed first.

---

## Required keys

These must be set (typically in `src/voice-agent/.env`) before the voice
agent will start:

| Variable | Description |
|---|---|
| `LIVEKIT_URL` | LiveKit server WebSocket URL, e.g. `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |

At least one LLM provider key is required:

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (recommended — lowest latency via prompt caching) |
| `GROQ_API_KEY` | Groq (first fallback; also used for Whisper STT and Orpheus TTS) |
| `OPENAI_API_KEY` | OpenAI (optional third rung) |
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | Google / Gemini |
| `DEEPSEEK_API_KEY` | DeepSeek (second fallback) |
| `MOONSHOT_API_KEY` | Kimi / Moonshot (experimental; gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`) |

Strongly recommended:

| Variable | Description |
|---|---|
| `DEEPGRAM_API_KEY` | Deepgram Nova-3 streaming STT. Without it the system falls back to Groq Whisper (non-streaming, no STT-confirmed barge-in). |

---

## JARVIS_* flags — grouped by subsystem

### LLM model / route overrides

Per-route model selection. Each accepts a `provider/model-name` string
(same format as `JARVIS_MODEL`). Defaults come from
`src/voice-agent/providers/llm.py::SPEECH_MODELS`.

| Variable | Default route |
|---|---|
| `JARVIS_MODEL` | Global default model (supervisor) |
| `JARVIS_PROVIDER` | Global default provider |
| `JARVIS_BANTER_MODEL` | BANTER route |
| `JARVIS_TASK_MODEL` | TASK route |
| `JARVIS_REASONING_MODEL` | REASONING route |
| `JARVIS_EMOTIONAL_MODEL` | EMOTIONAL route |
| `JARVIS_BROWSER_MODEL` | Browser-task subtool |
| `JARVIS_TASK_BROWSER_MODEL` | Browser sub-task model |
| `JARVIS_TASK_CODE_MODEL` | Code-execution sub-task model |
| `JARVIS_TASK_DESKTOP_MODEL` | Desktop / computer-use sub-task model |
| `JARVIS_TASK_FILES_MODEL` | File-ops sub-task model |
| `JARVIS_TASK_OTHER_MODEL` | Catch-all sub-task model |
| `JARVIS_ROUTER_MODEL` | Turn-router classifier model |
| `JARVIS_ROUTER_PROVIDER` | Turn-router classifier provider |
| `JARVIS_ROUTER_TIMEOUT_MS` | Router classification timeout |
| `JARVIS_DS_FALLBACK_MODEL` | DeepSeek fallback model name |
| `JARVIS_VALIDATOR_MODEL` | Proposal-validation model |
| `JARVIS_CRON_PROMPT_MODEL` | Cron-digest prompt model |
| `JARVIS_BROWSER_PROVIDER` | Browser-task LLM provider |
| `JARVIS_OLLAMA_URL` | Ollama base URL (for local models) |
| `JARVIS_OLLAMA_VISION_MODEL` | Ollama vision model name |
| `JARVIS_PROXY_URL` | LLM proxy URL (e.g. LiteLLM) |
| `JARVIS_PIN_ALL_ROUTES` | `1` — pin all routes to the global model |
| `JARVIS_MODEL_DEFINITIONS` | Path to custom model-definition JSON |
| `JARVIS_MODEL_REGISTRY_ENABLED` | `1` — enable the model registry tray switcher |
| `JARVIS_LANGGRAPH_SUPERVISOR` | `1` — use LangGraph supervisor path |
| `JARVIS_KIMI_VOICE_EXPERIMENTAL` | `1` — enable Kimi K2.6 voice (currently broken) |

### Kill-switches / feature gates

Set to `1` to disable the named feature. Safe defaults are feature ON.

| Variable | What it disables |
|---|---|
| `JARVIS_GRAPH_DISABLED` | LangGraph slow-path dispatcher (`pipeline/turn_graph.py`) |
| `JARVIS_TOKEN_AWARE_PRUNE` | `0` disables token-aware chat_ctx pruning |
| `JARVIS_MEMORY_CONSOLIDATOR` | `0` disables the memory consolidator |
| `JARVIS_CONFAB_STRICT_DISABLED` | Reverts confab detector to legacy permissive mode |
| `JARVIS_CONFAB_DETECTOR` | `0` disables the confab detector entirely |
| `JARVIS_CONFAB_SAVE_DISABLED` | `0` disables confab-detected turn saves |
| `JARVIS_DISPATCH_DISABLED` | `1` disables out-of-process dispatch_agent tool |
| `JARVIS_SELF_IMPROVE_DISABLED` | `1` disables self-improvement / evolution loop |
| `JARVIS_PROCEDURE_CAPTURE_DISABLED` | `1` disables procedure capture in memory layer |
| `JARVIS_CRON_DISABLED` | `1` disables the cron/scheduler subsystem |
| `JARVIS_EPHEMERAL_SYSTEM_PROMPT` | `1` — system prompt not persisted to chat_ctx |
| `JARVIS_PLUGINS_DISABLED` | `1` disables bundled plugins |
| `JARVIS_USER_PLUGINS` | Comma-separated list of user plugin paths to load |
| `JARVIS_BUNDLED_PLUGINS` | Comma-separated list of bundled plugins to enable |
| `JARVIS_SKIP_CDP` | `1` — skip Chrome DevTools Protocol browser check |
| `JARVIS_PRE_TTS_CONFAB_GATE` | `0` disables pre-TTS confab gate |
| `JARVIS_HANDOFF_CROSS_STREAM_GUARD` | `0` disables cross-stream handoff guard |
| `JARVIS_ECHO_AWARE_BARGEIN` | `0` disables echo-aware barge-in gate |
| `JARVIS_RECALL_TRIGGER_LIVE` | `1` enables live recall trigger (experimental) |
| `JARVIS_SAVE_TRIGGER_LIVE` | `1` enables live save trigger (experimental) |
| `JARVIS_STALE_STT_AUTO_RESTART` | `1` enables auto-restart on stale STT |
| `JARVIS_REPUBLISH_ON_AGENT_REJOIN` | `1` re-publishes audio track on agent rejoin |
| `JARVIS_LANG_AUTODETECT` | `0` disables language auto-detection |
| `JARVIS_TERMINAL_UNRESTRICTED` | `1` — removes named-action restriction on terminal tool (DANGER) |
| `JARVIS_WEB_ALLOW_PRIVATE` | `1` — allows web_fetch to reach private/loopback IPs |

### Memory subsystem (`JARVIS_MEMORY_*`, `JARVIS_CURATOR_*`)

| Variable | Description |
|---|---|
| `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` | Run consolidator every N successful extractions (default: 10) |
| `JARVIS_MEMORY_CONSOLIDATOR` | `0` disables consolidator |
| `JARVIS_MEMORY_PROVIDER` | Memory backend provider name |
| `JARVIS_MEMORY_TOP_N` | Number of memories to surface per recall query |
| `JARVIS_CURATOR_DISABLED` | `1` disables the memory curator |
| `JARVIS_CURATOR_INTERVAL_HOURS` | Curator run interval in hours |
| `JARVIS_CURATOR_MIN_IDLE_HOURS` | Minimum idle hours before curator runs |
| `JARVIS_CURATOR_STALE_AFTER_DAYS` | Mark memories stale after N days |
| `JARVIS_CURATOR_ARCHIVE_AFTER_DAYS` | Archive memories after N days |
| `JARVIS_CURATOR_BACKUP_DISABLED` | `1` skips backup before curator writes |
| `JARVIS_CURATOR_BACKUP_KEEP` | Number of curator backups to retain |
| `JARVIS_CURATOR_CONSOLIDATION` | `0` disables consolidation step in curator |
| `JARVIS_CURATOR_` | (prefix) additional curator sub-flags |

### Auto-mod loop (`JARVIS_AUTOMOD_*`)

The auto-mod loop is off by default. See `CLAUDE.md` for the hard blocklist.

| Variable | Description |
|---|---|
| `JARVIS_AUTOMOD_ENABLED` | `1` activates pattern detector + `propose_code_mod` voice tool |
| `JARVIS_AUTOMOD_SPAWN_LIVE` | `1` enables subprocess spawner (default: shadow mode) |
| `JARVIS_AUTOMOD_DAILY_CAP` | Max PRs per day (default: 3) |
| `JARVIS_AUTOMOD_PATTERN_INTERVAL_S` | Pattern detector poll interval |
| `JARVIS_AUTOMOD_MAX_DIFF_LINES` | Max diff lines per auto-mod proposal |
| `JARVIS_AUTOMOD_MAX_FILES` | Max files per auto-mod proposal |
| `JARVIS_AUTOMOD_ERROR_IGNORE_EXC` | Exception class names to ignore in error-driven branch |

### Cron / scheduler (`JARVIS_CRON_*`)

| Variable | Description |
|---|---|
| `JARVIS_CRON_DISABLED` | `1` disables the cron subsystem |
| `JARVIS_CRON_TICK_S` | Cron tick interval in seconds |
| `JARVIS_CRON_PENDING_POLL_S` | Pending-job poll interval |
| `JARVIS_CRON_MAX_JOBS` | Maximum concurrent cron jobs |
| `JARVIS_CRON_MAX_FAILURES` | Max consecutive failures before a job is suspended |
| `JARVIS_CRON_SCRIPT_TIMEOUT` | Per-job timeout in seconds |
| `JARVIS_CRON_DIGEST_MAX` | Max digest entries per cron report |
| `JARVIS_CRON_PROMPT_MODEL` | Model used for cron-digest prompts |

### Out-of-process dispatch agent (`JARVIS_DISPATCH_*`)

| Variable | Description |
|---|---|
| `JARVIS_DISPATCH_DISABLED` | `1` disables the dispatch_agent tool |
| `JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S` | Timeout for `explore` subagent |
| `JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S` | Timeout for `researcher` subagent |
| `JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S` | Timeout for `code_reviewer` subagent |
| `JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S` | Timeout for `plan` subagent |

### VAD / audio processing

| Variable | Description |
|---|---|
| `JARVIS_VAD_ACTIVATION_THRESHOLD` | Silero VAD activation threshold (0–1) |
| `JARVIS_VAD_DEACTIVATION_THRESHOLD` | Silero VAD deactivation threshold (0–1) |
| `JARVIS_VAD_MIN_SPEECH_S` | Minimum speech duration (seconds) to trigger STT |
| `JARVIS_VAD_MIN_SILENCE_S` | Minimum silence duration to end utterance |
| `JARVIS_VAD_PREFIX_PAD_S` | Pre-speech padding (seconds) |
| `JARVIS_AUDIO_INPUT_DEVICE` | ALSA/PipeWire input device name or index |
| `JARVIS_AUDIO_OUTPUT_DEVICE` | ALSA/PipeWire output device name or index |
| `JARVIS_AUDIO_SILENCE_CHECK_INTERVAL_S` | Silence monitor check interval |
| `JARVIS_AUDIO_SILENCE_TIMEOUT_S` | Silence timeout before idle action |
| `JARVIS_LISTENING_HOLD_S` | Hold-open duration after utterance in listening state |
| `JARVIS_LISTENING_RMS_THRESHOLD` | RMS threshold for listening state |
| `JARVIS_LISTENING_RMS_` | (prefix) additional RMS sub-flags |
| `JARVIS_SPEAKING_HOLD_S` | Hold-open duration in speaking state |
| `JARVIS_SPEAKING_PCM_RMS` | PCM RMS gate during speaking |
| `JARVIS_SPEAKING_RMS_THRESHOLD` | RMS threshold in speaking state |
| `JARVIS_MIC_DURING_SPEAK` | `1` — keep mic active during TTS playback |

### Echo cancellation (AEC) cascade (`JARVIS_APM_*`, `JARVIS_*_AEC`, `JARVIS_PIPEWIRE_AEC`)

| Variable | Description |
|---|---|
| `JARVIS_APM_AEC` | `1` enables APM acoustic echo cancellation |
| `JARVIS_APM_AGC` | `1` enables APM automatic gain control |
| `JARVIS_APM_NS` | `1` enables APM noise suppression |
| `JARVIS_APM_HPF` | `1` enables APM high-pass filter |
| `JARVIS_APM_DELAY_BIAS_MS` | APM delay bias (ms) |
| `JARVIS_NEURAL_AEC` | `1` enables neural AEC (RNNoise / other backend) |
| `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` | Neural AEC latency budget |
| `JARVIS_PIPEWIRE_AEC` | `1` uses PipeWire echo-cancel source |
| `JARVIS_AEC_FORCE_PROFILE` | Force a named AEC profile |
| `JARVIS_ECHO_AWARE_BARGEIN` | `0` disables echo-aware barge-in gate |
| `JARVIS_ECHO_MIN_NOVEL` | Minimum novelty score for echo-aware barge-in |

### Screen observer / screen share

| Variable | Description |
|---|---|
| `JARVIS_SCREEN_OBSERVER_ENABLED` | `1` enables the background screen observer |
| `JARVIS_SCREEN_OBSERVER_MODE` | Observer mode: `passive` / `active` / etc. |
| `JARVIS_SCREEN_OBSERVER_INTERVAL_S` | Screenshot capture interval |
| `JARVIS_SCREEN_OBSERVER_MAX_AGE_S` | Max age of an observation before it is discarded |
| `JARVIS_SCREEN_OBSERVER_LIVE_MODEL` | Model used for live screen analysis |
| `JARVIS_SCREEN_OBSERVER_STREAM_FRAME_INTERVAL_S` | Frame interval in stream mode |
| `JARVIS_SCREEN_OBSERVER_STREAM_PROMPT_INTERVAL_S` | Prompt interval in stream mode |
| `JARVIS_SCREEN_SHARE_DISPLAY` | X11 display to capture (e.g. `:0`) |
| `JARVIS_SCREEN_SHARE_FFMPEG` | `1` uses FFmpeg for screen capture |
| `JARVIS_SCREEN_SHARE_FPS` | Screen share frame rate |
| `JARVIS_SCREEN_SHARE_HEIGHT` | Screen share height |
| `JARVIS_SCREEN_SHARE_WIDTH` | Screen share width |
| `JARVIS_SCREEN_SHARE_IDENTITY` | LiveKit track identity for screen share |
| `JARVIS_SCREENSHOT_JPEG_Q` | JPEG quality for screenshots (0–100) |
| `JARVIS_SCREENSHOT_MAX_EDGE` | Maximum edge length for screenshots (px) |

### Computer-use tool (`JARVIS_COMPUTER_USE_*`)

| Variable | Description |
|---|---|
| `JARVIS_COMPUTER_USE_BACKEND` | Backend: `anthropic` / `local` |
| `JARVIS_COMPUTER_USE_MONITOR` | Monitor index for multi-display setups |
| `JARVIS_COMPUTER_USE_TIMEOUT` | Per-action timeout (seconds) |
| `JARVIS_COMPUTER_USE_XDOTOOL` | `0` disables xdotool usage in computer_use |
| `JARVIS_VISION_BACKEND` | Vision backend for screen reading |

### TTS / voice output

| Variable | Description |
|---|---|
| `JARVIS_TTS_VOICE` | Default TTS voice name (e.g. Orpheus voice slug) |
| `JARVIS_EDGE_VOICE` | Edge TTS voice (fallback) |
| `JARVIS_FR_EDGE_VOICE` | Edge TTS voice for French output |
| `JARVIS_VOICE_BANTER` | Per-route TTS voice for BANTER |
| `JARVIS_VOICE_TASK` | Per-route TTS voice for TASK |
| `JARVIS_VOICE_REASONING` | Per-route TTS voice for REASONING |
| `JARVIS_VOICE_EMOTIONAL` | Per-route TTS voice for EMOTIONAL |
| `JARVIS_VOICE_IDENTITY` | Voice identity string (used in prompts) |
| `JARVIS_PLAYBACK_LATENCY_S` | TTS playback buffer latency |
| `JARVIS_DEBUG_TTS_CHUNKS` | `1` logs TTS chunk timing |
| `JARVIS_TTFW_TARGET_MS` | Target time-to-first-word latency (ms) |

### Bridge / network

| Variable | Description |
|---|---|
| `JARVIS_BRIDGE_URL` | URL of the local bridge (`http://127.0.0.1:8765`) |
| `JARVIS_REQUIRE_LOCAL_AUTH` | `1` enforces bearer-token auth on bridge |
| `JARVIS_LOCAL_API_TOKEN` | Bearer token for bridge auth (read from `~/.jarvis/local-api-token.env`) |
| `JARVIS_SECRET_TOKEN` | Secondary secret (context-dependent) |
| `JARVIS_RPC_SOCKET` | Unix socket path for RPC |
| `JARVIS_EXT_TIMEOUT_MS` | Extension call timeout (ms) |
| `JARVIS_BROWSER_CDP_URL` | Chrome DevTools Protocol URL |
| `JARVIS_VOICE_CLIENT_PORT` | Port the voice client listens on |
| `JARVIS_VOICE_CLIENT_URL` | URL the voice client exposes |
| `JARVIS_WORKER_PORT` | Voice-agent HTTP worker port |

### LiveKit / room

| Variable | Description |
|---|---|
| `JARVIS_VOICE_ROOM` | LiveKit room name |
| `JARVIS_VOICE_SESSION_ID` | Voice session ID (injected at startup) |
| `JARVIS_VOICE_TOKEN_TTL_HOURS` | LiveKit token TTL |

### LLM context / prompt

| Variable | Description |
|---|---|
| `JARVIS_INSTRUCTIONS` | Runtime system-prompt override (injected at startup) |
| `JARVIS_ANTHROPIC_STABLE_CACHE_TTL` | Anthropic prompt-cache TTL override |
| `JARVIS_CACHE_BREAK` | `1` forces cache-break on next turn |
| `JARVIS_LLM_IDLE_TIMEOUT` | LLM idle timeout (seconds) |
| `JARVIS_EPHEMERAL_SYSTEM_PROMPT` | `1` — system prompt not persisted |
| `JARVIS_CLI_VOICE_PROMPT` | CLI voice mode system prompt override |
| `JARVIS_CLI_SCRIPT` | CLI agent script path |
| `JARVIS_CLI_TIMEOUT_S` | CLI agent call timeout |

### Skills

| Variable | Description |
|---|---|
| `JARVIS_SKILL_DIR` | Path to user skill directory |
| `JARVIS_SKILLS_PATHS` | Colon-separated list of skill search paths |
| `JARVIS_SKILL_REVIEW_APPLY` | `1` auto-applies skill review suggestions |
| `JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS` | Character threshold for long-reply detection |

### STT

| Variable | Description |
|---|---|
| `JARVIS_STT_KEYTERMS` | Comma-separated keyterms hint for Deepgram |

### Code execution

| Variable | Description |
|---|---|
| `JARVIS_CODE_EXEC_ENV_PASSTHROUGH` | Comma-separated env vars to pass into sandboxed code execution |
| `JARVIS_CODE_EXEC_MAX_TOOL_CALLS` | Max tool calls per code-exec session |
| `JARVIS_CODE_EXEC_TIMEOUT` | Code execution timeout (seconds) |

### File ops

| Variable | Description |
|---|---|
| `JARVIS_FILE_READ_MAX_CHARS` | Maximum characters returned by `read_file` |
| `JARVIS_WRITE_SAFE_ROOT` | Root directory that `write_file` is restricted to |

### Data / paths

| Variable | Description |
|---|---|
| `JARVIS_DATA_DIR` | Override for `~/.local/share/jarvis/` |
| `JARVIS_HOME` | Override for `~/.jarvis/` |
| `JARVIS_HUB_DB` | Path to hub SQLite DB |
| `JARVIS_TELEMETRY_PATH` | Path to turn telemetry SQLite DB |
| `JARVIS_TURN_TELEMETRY_DB` | Alias for `JARVIS_TELEMETRY_PATH` |

### Quiet window / idle

| Variable | Description |
|---|---|
| `JARVIS_QUIET_START` | Hour (0–23) quiet window starts |
| `JARVIS_QUIET_END` | Hour (0–23) quiet window ends |
| `JARVIS_QUIET_WINDOW_SEC` | Quiet window duration in seconds |

### Output / language

| Variable | Description |
|---|---|
| `JARVIS_OUTPUT_NON_LATIN_MIN_LEN` | Minimum non-Latin token length to trigger transliteration |
| `JARVIS_OUTPUT_NON_LATIN_THRESHOLD` | Fraction threshold for non-Latin output gate |
| `JARVIS_NAME_RE` | Regex pattern matching the assistant's name (for wake-word) |

### Face / biometrics

| Variable | Description |
|---|---|
| `JARVIS_FACE_ENROLL_FRAMES` | Frames to capture during face enrolment |
| `JARVIS_FACE_LIVENESS_FRAMES` | Frames for liveness check |
| `JARVIS_FACE_THRESHOLD` | Face-recognition match threshold |
| `JARVIS_WEBCAM_DEVICE` | Webcam device path or index |
| `JARVIS_WEBCAM_RES` | Webcam resolution (e.g. `640x480`) |

### XAI / web-search hardening (`JARVIS_XAI_*`)

| Variable | Description |
|---|---|
| `JARVIS_XAI_WEB_MODEL` | Model for XAI web-search tool |
| `JARVIS_XAI_WEB_TIMEOUT` | Timeout for XAI web-search (seconds) |
| `JARVIS_XAI_ALLOWED_DOMAINS` | Allowlist of domains for XAI fetches |
| `JARVIS_XAI_ALLOWED_DOMAINS_ENV` | Env var name that contains the allowlist |
| `JARVIS_XAI_EXCLUDED_DOMAINS` | Blocklist of domains for XAI fetches |
| `JARVIS_XAI_EXCLUDED_DOMAINS_ENV` | Env var name that contains the blocklist |
| `JARVIS_XAI_MODEL_ENV` | Env var name that overrides the XAI model |
| `JARVIS_XAI_TIMEOUT_ENV` | Env var name that overrides the XAI timeout |

### Observability / telemetry

| Variable | Description |
|---|---|
| `JARVIS_VOICE_LOG_LEVEL` | Log level for voice agent (`DEBUG` / `INFO` / `WARNING`) |
| `JARVIS_LANGFUSE_PUBLIC_KEY` | Langfuse public key (tracing) |
| `JARVIS_LANGFUSE_SECRET_KEY` | Langfuse secret key (tracing) |
| `JARVIS_OSINT_CACHE` | Path to OSINT cache directory |
| `JARVIS_OSINT_UA` | User-agent string for OSINT fetches |

### ACP (Agent Client Protocol)

| Variable | Description |
|---|---|
| `JARVIS_ACP_PERMISSIONS` | `permissive` — skip approval dialogs in ACP mode |

### Misc / internal

| Variable | Description |
|---|---|
| `JARVIS_VERSION` | Version string injected at build time |
| `JARVIS_USER_UID` | UID of the invoking user (injected by installer) |
| `JARVIS_IR_DEVICE` | Infrared device path (home-automation) |
| `JARVIS_MEET_ENABLED` | `1` enables Google Meet integration |
| `JARVIS_TEST_TOKEN` | Token used by integration tests |
| `JARVIS_RUN_INTEGRATION` | `1` runs integration tests (not just unit tests) |
| `JARVIS_HOOK_EVENT` | Hook event name (used by `.claude/hooks/`) |
| `JARVIS_HOOK_PAYLOAD_JSON` | Hook payload JSON |
| `JARVIS_SUBAGENT_` | (prefix) subagent-specific flags |
| `JARVIS_VOICE_` | (prefix) additional voice-layer flags |
| `JARVIS_CURATOR_` | (prefix) curator sub-flags |
| `JARVIS_LISTENING_RMS_` | (prefix) RMS listening sub-flags |

---

## Notes

- All `JARVIS_*` vars are read at **runtime** (not baked at build time) unless
  noted otherwise. You can change them in `.env` and restart the service.
- Vars marked `kill-switch` default to the feature being ON; set `0` or `1`
  as described to change behaviour.
- Provider keys (`ANTHROPIC_API_KEY`, etc.) can also be placed in
  `~/.jarvis/keys.env` — that file is sourced after `src/voice-agent/.env`
  and takes precedence. Rotate stale keys in both locations.
- For the hub DB path and log locations see `docs/2026-05-17-jarvis-repo-map.md §5`.

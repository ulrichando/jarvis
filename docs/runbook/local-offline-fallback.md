# Local offline fallback stack (LLM / STT / TTS / Vision)

**Status:** implemented + live-verified on Kali 2026-06-17. Default OFF.
**Design:** `~/.claude/plans/we-need-to-find-polymorphic-allen.md` (2026-06-15).

JARVIS normally runs on cloud providers (Anthropic / Groq / DeepSeek for
LLM, Deepgram + Groq Whisper for STT, Groq Orpheus + Edge for TTS,
Anthropic for vision). This stack adds a **last-resort LOCAL rung** to
each component so JARVIS keeps working with no internet at all. Every
rung is independently gated and **OFF by default** — cloud stays primary.

```
                CLOUD (primary)                      LOCAL (this stack, last rung)
LLM    Anthropic → Groq → DeepSeek          →  Ollama (any model)        [rung-0 PRIMARY when on]
STT    Deepgram Nova-3 → Groq Whisper        →  faster-whisper (ctranslate2)
TTS    Groq Orpheus → Edge-TTS               →  Piper (onnxruntime)
Vision Anthropic vision (webcam tool)        →  Ollama vision (moondream / llava / qwen2.5-vl)
```

Each rung degrades **independently** via the existing `FallbackAdapter`
pattern — there is no single "offline switch". A DOWN endpoint fails fast
(`APIConnectionError`) and cascades; a reachable-but-slow one is bounded
by the per-rung timeout.

> ⚠️ The **LLM** rung is special: when `JARVIS_LOCAL_LLM_ENABLED=1` and
> the startup `/v1/models` probe confirms the endpoint/model, the local
> model is **rung-0 (PRIMARY)** — tried *first*, ahead of cloud. The
> STT/TTS/Vision rungs are genuine *last* resorts (only hit when the cloud
> rungs fail).

---

## Environment variables

All declared in `pipeline/config.py`; read live by the providers. Set in
`src/voice-agent/.env` (gitignored). A commented-ready block is at the
bottom of that file.

| Var | Default | Purpose |
|---|---|---|
| `JARVIS_LOCAL_LLM_ENABLED` | `0` | Master switch for the local LLM rung-0. |
| `JARVIS_LOCAL_LLM_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compat endpoint. Remote box → `http://WIN_IP:11434/v1`. |
| `JARVIS_LOCAL_LLM_MODEL` | `qwen3:14b` | Ollama model tag. |
| `JARVIS_LOCAL_LLM_API_KEY` | `ollama` | Auth header (Ollama ignores it; vLLM may need a real key). |
| `JARVIS_LOCAL_LLM_TIMEOUT` | `60` | Per-request seconds. Generous for cold/big-model loads; a down endpoint still fails fast. |
| `JARVIS_LOCAL_LLM_PROBE_TIMEOUT` | `1.0` | Startup probe timeout for `GET /v1/models`; the local rung is skipped if the endpoint/model is absent. |
| `JARVIS_LOCAL_LLM_ASSUME_AVAILABLE` | `0` | Test/manual escape hatch: skip the startup probe and trust the configured endpoint/model. |
| `JARVIS_LOCAL_LLM_ROUTES` | *(empty=all)* | CSV subset of routes (e.g. `BANTER,TASK_CODE`). |
| `JARVIS_LOCAL_STT_ENABLED` | `0` | faster-whisper local STT last rung. |
| `JARVIS_LOCAL_STT_MODEL` | `large-v3` | Whisper size (`base`/`small`/`large-v3`). |
| `JARVIS_LOCAL_STT_DEVICE` | `cpu` | `cpu` (robust, no cuDNN) or `cuda`. |
| `JARVIS_LOCAL_STT_COMPUTE` | `int8` | `int8` (cpu) / `float16` (cuda). |
| `JARVIS_LOCAL_TTS_ENABLED` | `0` | Piper local TTS last rung. |
| `JARVIS_LOCAL_TTS_ENGINE` | `piper` | `piper` (only one implemented; `kokoro` reserved). |
| `JARVIS_LOCAL_TTS_MODEL_PATH` | `~/.jarvis/models/piper/en_US-lessac-medium.onnx` | Piper voice `.onnx`. |
| `JARVIS_LOCAL_VISION_ENABLED` | `0` | Ollama vision fallback for the `webcam` tool. |
| `JARVIS_OLLAMA_VISION_MODEL` | `llava` | Ollama vision model tag. |
| `JARVIS_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama root (normalized to `/v1` internally). |

---

## Kali setup (done 2026-06-17)

Box: i9-10885H, 62 GB RAM, **RTX 2060 Max-Q (6 GB VRAM)**. The 6 GB VRAM
is the constraint — small models only; big models are for the Windows
box. Functional proof here, performance there.

### 1. Ollama (LLM + vision)
```bash
curl -fsSL https://ollama.com/install.sh | sh      # systemd service, auto-detects NVIDIA GPU
ollama pull llama3.1:8b      # LLM functional test (~4.9 GB, solid tool calling, fits 6 GB)
ollama pull moondream        # vision fallback (~1.7 GB, fast)
systemctl is-active ollama && curl -s 127.0.0.1:11434/api/version
```
> Windows 256 GB box: `ollama pull qwen3:72b` (or `llama3.3:70b`,
> `qwen2.5-vl:7b` for vision). Speed there is GPU-VRAM-bound, not RAM.

### 2. faster-whisper (STT) — into the voice-agent venv
All-new deps (ctranslate2 / tokenizers / huggingface-hub) — does **not**
disturb the pinned `livekit-agents`. Verified.
```bash
cd src/voice-agent && .venv/bin/pip install faster-whisper
```
Model downloads from HF on first transcription (cached under
`~/.cache/huggingface`). CPU/int8 by default — no VRAM contention with
the LLM, no cuDNN requirement.

### 3. Piper (TTS) — into the voice-agent venv
`piper-tts 1.4.x` ships a `cp39-abi3` wheel (py3.13-OK); only new dep is
`pathvalidate`. Verified.
```bash
cd src/voice-agent && .venv/bin/pip install piper-tts
mkdir -p ~/.jarvis/models/piper
.venv/bin/python -m piper.download_voices en_US-lessac-medium --download-dir ~/.jarvis/models/piper
```

### 4. Enable + restart
Uncomment the desired blocks in `src/voice-agent/.env` (see bottom of
that file), then — **only when no voice session is active** (check
`turn_telemetry.db` latest `ts_utc` is >60 s old):
```bash
systemctl --user restart jarvis-voice-agent.service
```

---

## Verify

```bash
cd src/voice-agent
# Unit tests for the gating/wiring (fast, no models):
.venv/bin/python -m pytest tests/test_local_offline_fallback.py -q

# Live LLM tool-call against local Ollama:
.venv/bin/python - <<'PY'
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
r = c.chat.completions.create(model="llama3.1:8b",
    messages=[{"role":"user","content":"search the web for today's news"}],
    tools=[{"type":"function","function":{"name":"web_search","description":"search",
        "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}}])
print(r.choices[0].finish_reason, r.choices[0].message.tool_calls)
PY
```
With the service running + flags on, confirm startup logs show the local
LLM probe passing and telemetry `model` shows `local:<model>` for the scoped
routes. If `/v1/models` is unreachable or does not advertise the configured
model, the dispatcher skips the local rung and logs `local LLM requested but
unavailable`. STT/TTS still show `[stt.local]` / `[tts.local]` lines in
`~/.local/share/jarvis/logs/voice-agent.log`.

---

## Windows WSL2 mirror (the real GPU server)

Goal: run the heavy LLM (and optionally vision) on the 256 GB Windows box
and point Kali's JARVIS at it over the LAN. Two ways to host Ollama on
Windows; **native is simplest, WSL2 gives Linux-GPU parity**.

### Option A — Ollama in WSL2 (Linux parity, recommended for dev)
1. Ensure WSL2 + an NVIDIA GPU with the **CUDA-on-WSL** driver (install
   the normal Windows NVIDIA driver; WSL2 sees the GPU automatically —
   verify with `nvidia-smi` *inside* WSL2). Do **not** install a Linux
   GPU driver inside WSL.
2. In the WSL2 distro:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull qwen3:72b          # or llama3.3:70b; qwen2.5-vl:7b for vision
   ```
3. Bind Ollama to all interfaces so the LAN (and the Windows host) can
   reach it. Create `~/.config/systemd/user/` override or run with env:
   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```
   (For the install's systemd service: `systemctl edit ollama` and add
   `Environment=OLLAMA_HOST=0.0.0.0:11434`, then restart.)
4. **WSL2 networking:** WSL2 is NAT'd behind the Windows host. To reach
   the WSL2 Ollama from Kali on the LAN, add a Windows **portproxy** so
   the Windows host forwards `:11434` into WSL2. In an **Admin
   PowerShell** on Windows:
   ```powershell
   $wsl = (wsl hostname -I).Trim().Split()[0]      # WSL2 internal IP
   netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=11434 connectaddress=$wsl connectport=11434
   netsh advfirewall firewall add rule name="Ollama 11434" dir=in action=allow protocol=TCP localport=11434
   ```
   (Re-run the portproxy line after a reboot — the WSL2 IP changes; or
   script it on login. A static WSL2 IP / `wsl --update` mirrored-mode
   networking avoids this.)

### Option B — native Windows Ollama (no WSL)
Install `https://ollama.com/download/windows` (tray service, autostarts).
Set a Windows **environment variable** `OLLAMA_HOST=0.0.0.0:11434`,
restart the service, and open the firewall:
```powershell
netsh advfirewall firewall add rule name="Ollama 11434" dir=in action=allow protocol=TCP localport=11434
```

### Point Kali JARVIS at the Windows box
In `src/voice-agent/.env`:
```env
JARVIS_LOCAL_LLM_ENABLED=1
JARVIS_LOCAL_LLM_URL=http://WINDOWS_LAN_IP:11434/v1
JARVIS_LOCAL_LLM_MODEL=qwen3:72b
```
Sanity-check reachability from Kali before restarting the agent:
```bash
curl -s http://WINDOWS_LAN_IP:11434/api/version
```

> **Security:** Ollama has **no auth**. Keep `0.0.0.0:11434` on a trusted
> LAN / VPN only — never port-forward it to the public internet without a
> reverse proxy (nginx basic-auth or mTLS) in front.

STT (faster-whisper) and TTS (Piper) stay **on Kali** in-process — they
don't benefit from the remote GPU the way the LLM does, and keeping them
local means audio never leaves the box.

---

## Auto model selection (`hwfit`)

Set `JARVIS_LOCAL_LLM_MODEL=auto` (or pick **Local · Auto** in the tray) and
JARVIS scans VRAM + RAM and picks the best-fitting *tool-capable* Ollama tag —
no guessing which model fits. `providers/local_model_picker.py`.

| Detected VRAM | Picks | Notes |
|---|---|---|
| ~6 GB | `llama3.1:8b` | this Kali box (8% RAM-offload) |
| 12–16 GB | `qwen3:14b` | |
| 24–32 GB | `qwen3:32b` | |
| 48–96 GB | `llama3.3:70b` | reports Q6/Q8 headroom |
| 192 GB+ | `qwen3:235b-a22b` | MoE |

It models each candidate's footprint (Q4_K_M, Ollama's default) vs VRAM,
penalises RAM-offload (voice is latency-sensitive), and reports the highest
quant that still fits in VRAM (pull `tag-q8_0` etc. for more quality).
Returned tags are always real, pullable Ollama tags. **One-command setup on a
new box** (detect hardware → pick → `ollama pull` the recommendation):
```bash
bin/jarvis-local-setup            # add --dry-run to preview, -y to skip the prompt,
                                  # or --model <tag> to pull a specific one
```
> Note: the picker SELECTS + detects hardware but does NOT auto-pull on agent
> startup (a 70B/235B is 40–140 GB — that can't block boot). `jarvis-local-setup`
> is the explicit pull step. Or just inspect what it would pick:
```bash
.venv/bin/python -c "from providers.local_model_picker import recommend; print(recommend())"
```

## Kokoro TTS (`engine=kokoro`)

Kokoro-82M is higher-quality than Piper but **can't install into the pinned
venv** (it pins `numpy==1.26.4` vs the venv's 2.4.6, and its misaki→spacy→blis
G2P won't compile on every CPU). Run it as a separate OpenAI-compatible
server and point JARVIS at it — same as Odysseus's `endpoint:<id>` provider:
```bash
docker run -d --name kokoro-fastapi -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu   # or -gpu
```
```env
JARVIS_LOCAL_TTS_ENABLED=1
JARVIS_LOCAL_TTS_ENGINE=kokoro
JARVIS_LOCAL_TTS_URL=http://127.0.0.1:8880/v1   # remote box: http://GPU_IP:8880/v1
JARVIS_LOCAL_TTS_VOICE=af_heart
```
**Robustness note:** Kokoro needs its server up. **Piper** (`engine=piper`,
in-process, no external server) is the more robust *truly-offline* TTS — if
the box is offline AND the kokoro server is down, Kokoro has no voice. Use
Piper where offline-survival matters; Kokoro where a reliable GPU server runs.

## Files

| File | Role |
|---|---|
| `providers/llm.py` | LLM rung-0 injection (`_make_local_llm` in `build_dispatching_llm`) + `ollama/*` + `ollama/auto` tray entries. |
| `providers/local_model_picker.py` | `hwfit` quant-aware VRAM/RAM → best Ollama tag (`auto`). |
| `providers/kokoro_tts.py` | Kokoro via OpenAI-compat `/audio/speech` endpoint adapter. |
| `providers/faster_whisper_stt.py` | `FasterWhisperSTT` adapter + `build_local_stt`. |
| `providers/stt.py` | `build_stt_chain` appends the local rung. |
| `providers/piper_tts.py` | `PiperTTS` adapter + `build_local_tts`. |
| `providers/tts.py` | `build_tts_chain` + dispatching path append the local rung. |
| `vision/ollama_vision.py` | Local Ollama vision helper. |
| `tools/webcam.py` | `_analyze_jpeg` dispatches Anthropic → local; gate allows local-only. |
| `pipeline/config.py` | `LOCAL_*` env declarations. |
| `tests/test_local_offline_fallback.py` | Gating/wiring unit tests (21). |

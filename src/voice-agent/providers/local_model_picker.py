"""Hardware-aware local-model picker (`hwfit`) — Odysseus-inspired, 2026-06-17.

Lets `JARVIS_LOCAL_LLM_MODEL=auto` (or tray `ollama/auto`) resolve to the
best-fitting *tool-capable* Ollama model for the box: scan VRAM + RAM,
compute each candidate's memory footprint, and score by quality, fit
(VRAM-resident vs RAM-offloaded), and quant headroom — the same idea as
PewDiePie's Odysseus "Cookbook"/hwfit, scoped to JARVIS's tool-heavy
supervisor and to Ollama (so the returned tag always pulls).

Design choices vs Odysseus's 2500-line engine (kept deliberately tight):
  - Catalog of REAL Ollama tags (no fake `qwen3:72b`), largest→smallest.
  - Footprint = Q4_K_M weight (Ollama's default quant, ≈ the download size)
    + a small KV/activation overhead. Tag returned is the BASE tag (Q4_K_M),
    which Ollama always serves — so we never recommend a quant tag that may
    not exist.
  - QUANT tables (BPP/quality) ARE used, but for *headroom advice*: we report
    the highest quant that still fits in VRAM (pull `tag-q8_0` etc. for more
    quality), rather than minting unreliable quant tags.
  - VRAM-fit beats RAM-offload heavily (voice is latency-sensitive); a model
    that only fits via >85% RAM offload is excluded as too slow.

No model pulls, no build-time network calls — just resolves a tag.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("jarvis.llm.picker")

# Catalog: real Ollama tags, largest→smallest. Tool-capable models only
# (JARVIS is tool-heavy). q4_gb = on-disk/VRAM weight at Q4_K_M (Ollama's
# default) ≈ the `ollama pull` size. family_q = relative capability score.
#   (base_tag, params_b, q4_gb, ctx_k, family_q)
_MODELS: list[tuple[str, float, float, int, int]] = [
    ("llama3.1:405b",   405, 243.0, 128, 94),
    ("qwen3:235b-a22b", 235, 142.0,  32, 95),   # MoE (~22B active) — top tool model
    ("qwen2.5:72b",      72,  47.0,  32, 90),
    ("llama3.3:70b",     70,  43.0, 128, 92),
    ("qwen3:32b",        32,  20.0,  32, 86),
    ("qwen3:14b",        14,   9.0,  32, 80),
    ("llama3.1:8b",       8,   4.9, 128, 72),
    ("qwen2.5:7b",        7,   4.7,  32, 70),
    ("llama3.2:3b",       3,   2.0, 128, 55),
]

# GGUF quant tiers Ollama serves (best→worst quality) + bytes/param (incl
# overhead) and a quality delta. Used for VRAM-headroom advice only.
_QUANT_ORDER = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M"]
_QUANT_BPP = {"Q8_0": 1.05, "Q6_K": 0.80, "Q5_K_M": 0.68, "Q4_K_M": 0.58}
_Q4 = "Q4_K_M"

_MAX_OFFLOAD_FRAC = 0.85   # beyond this, RAM-offload is too slow to be useful
_VRAM_HEADROOM = 0.93      # leave room for context growth / the desktop


@dataclass(frozen=True)
class ModelPick:
    tag: str                 # base Ollama tag to use (Q4_K_M, always pullable)
    params_b: float
    vram_gb: float | None
    ram_gb: float | None
    footprint_gb: float      # estimated Q4_K_M memory footprint
    run_mode: str            # "gpu" (VRAM-resident) | "offload" | "cpu"
    offload_frac: float      # 0.0 when VRAM-resident
    headroom_quant: str      # highest quant that still fits VRAM (advice)
    note: str
    available: bool | None = None


def detect_vram_gb() -> float | None:
    """Total VRAM of the largest NVIDIA GPU (GiB). None when no NVIDIA GPU.
    Handles the WSL2 path where nvidia-smi lives at /usr/lib/wsl/lib/."""
    smi = shutil.which("nvidia-smi") or _wsl_nvidia_smi()
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        vals = [float(x) for x in out.split() if x.strip().replace(".", "").isdigit()]
        return round(max(vals) / 1024.0, 1) if vals else None  # MiB → GiB
    except Exception as e:  # noqa: BLE001 — detection must never break boot
        logger.debug("[picker] VRAM detection failed: %s", e)
        return None


def _wsl_nvidia_smi() -> str | None:
    """On WSL2, nvidia-smi is at /usr/lib/wsl/lib/ and often not on PATH
    (borrowed from Odysseus's hwfit/hardware.py WSL handling)."""
    p = "/usr/lib/wsl/lib/nvidia-smi"
    return p if os.path.exists(p) else None


def detect_ram_gb() -> float | None:
    """Total system RAM in GiB. Linux /proc/meminfo first; psutil fallback
    for Windows / macOS (which have no /proc). Detection must never raise."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024.0 * 1024.0), 1)  # kB → GiB
    except Exception:  # noqa: BLE001
        pass
    # Windows / macOS have no /proc/meminfo. psutil is a core dependency
    # (requirements.txt) and is cross-platform. Without this fallback the
    # picker saw RAM=None on Windows and dropped to the 3B CPU floor even on
    # a 128 GB box (caught on the 2026-06-18 Windows deploy) — so local mode
    # picked a far weaker model than the hardware supports.
    try:
        import psutil  # noqa: PLC0415 — lazy so Linux import stays light
        return round(psutil.virtual_memory().total / (1024.0 ** 3), 1)
    except Exception:  # noqa: BLE001
        return None


def _overhead_gb(ctx_k: int) -> float:
    """KV-cache + activation overhead on top of weights, assuming a small
    working context (voice turns are short; we don't reserve the full 128k)."""
    return 1.0 + min(ctx_k, 8) * 0.08


def _footprint_gb(q4_gb: float, ctx_k: int) -> float:
    return q4_gb + _overhead_gb(ctx_k)


def _headroom_quant(q4_gb: float, ctx_k: int, vram_gb: float) -> str:
    """Highest quant whose weights still fit VRAM (quality advice)."""
    budget = vram_gb * _VRAM_HEADROOM - _overhead_gb(ctx_k)
    for q in _QUANT_ORDER:  # best→worst
        if q4_gb * (_QUANT_BPP[q] / _QUANT_BPP[_Q4]) <= budget:
            return q
    return _Q4


def analyze(vram_gb: float | None, ram_gb: float | None = None) -> list[ModelPick]:
    """Score every catalog model for this hardware, best→worst."""
    vram = vram_gb or 0.0
    ram = ram_gb or 0.0
    picks: list[tuple[float, ModelPick]] = []
    for tag, params_b, q4_gb, ctx_k, family_q in _MODELS:
        # fp already includes KV/activation overhead, so "fits in VRAM"
        # means footprint <= physical VRAM (no extra headroom subtraction —
        # that double-counted and produced negative offload fractions).
        fp = _footprint_gb(q4_gb, ctx_k)
        if vram > 0 and fp <= vram:
            run_mode, frac = ("gpu", 0.0)
            # Modest VRAM-resident bonus: enough to break ties toward the
            # faster option, small vs the 40-pt family-quality spread so a
            # bigger model that only LIGHTLY offloads still wins.
            score = family_q + 6.0
            hq = _headroom_quant(q4_gb, ctx_k, vram)
        elif vram > 0 and fp <= (vram + ram) * 0.90:
            frac = (fp - vram) / fp
            if frac > _MAX_OFFLOAD_FRAC:
                continue  # too slow to be useful for voice
            run_mode, score, hq = "offload", family_q - frac * 32.0, _Q4
        elif vram == 0 and fp <= ram * 0.80:
            # No GPU at all: CPU-only. Only small-ish models are tolerable.
            if params_b > 9:
                continue
            run_mode, frac, score, hq = "cpu", 1.0, family_q - 30.0, _Q4
        else:
            continue
        note = (
            f"{params_b:g}B @ Q4_K_M ≈ {fp:.0f}GB, "
            + ("VRAM-resident" if run_mode == "gpu" else
               f"{run_mode} ({frac*100:.0f}% off-GPU)")
            + (f"; Q{hq[1:]} fits VRAM for more quality" if run_mode == "gpu" and hq != _Q4 else "")
        )
        picks.append((score, ModelPick(
            tag=tag, params_b=params_b, vram_gb=vram_gb, ram_gb=ram_gb,
            footprint_gb=round(fp, 1), run_mode=run_mode, offload_frac=round(frac, 2),
            headroom_quant=hq, note=note,
        )))
    picks.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in picks]


def pick(vram_gb: float | None, ram_gb: float | None = None) -> tuple[str, str]:
    """Best (tag, note) for the given VRAM/RAM. Falls back to the smallest
    model when nothing scores (e.g. no GPU + tiny RAM)."""
    ranked = analyze(vram_gb, ram_gb)
    if ranked:
        return ranked[0].tag, ranked[0].note
    return "llama3.2:3b", "fallback floor (3B) — no fitting candidate found"


def recommend(*, check_available: bool = True) -> ModelPick:
    """Full hardware scan → best ModelPick (with availability when asked)."""
    vram, ram = detect_vram_gb(), detect_ram_gb()
    ranked = analyze(vram, ram)
    best = ranked[0] if ranked else ModelPick(
        tag="llama3.2:3b", params_b=3, vram_gb=vram, ram_gb=ram, footprint_gb=2.5,
        run_mode="cpu", offload_frac=1.0, headroom_quant=_Q4, note="fallback floor (3B)",
    )
    if check_available:
        object.__setattr__(best, "available", _is_pulled(best.tag))
    return best


def resolve_model_tag(configured: str) -> str:
    """Resolve `JARVIS_LOCAL_LLM_MODEL`. `auto` (any case) → hardware pick;
    any explicit tag passes through. Never raises."""
    if (configured or "").strip().lower() != "auto":
        return configured
    try:
        p = recommend(check_available=False)
        logger.info(
            "[picker] auto-selected %s (VRAM=%s GB, RAM=%s GB, %s) — %s",
            p.tag, p.vram_gb, p.ram_gb, p.run_mode, p.note,
        )
        return p.tag
    except Exception as e:  # noqa: BLE001
        logger.warning("[picker] auto-resolve failed (%s); using llama3.1:8b", e)
        return "llama3.1:8b"


def _is_pulled(tag: str) -> bool | None:
    """Best-effort: is `tag`'s model already in `ollama list`?"""
    ollama = shutil.which("ollama")
    if not ollama:
        return None
    try:
        out = subprocess.run(
            [ollama, "list"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
        base = tag.split(":")[0]
        return any(line.split()[0].split(":")[0] == base for line in out.splitlines()[1:] if line.strip())
    except Exception:  # noqa: BLE001
        return None

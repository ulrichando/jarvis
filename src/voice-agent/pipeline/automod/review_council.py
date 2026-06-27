"""3-lens review council for pending self-evolution proposals (2026-06-25).

When a build produces a `pending` proposal, an automatic council reviews its
diff through three INDEPENDENT lenses — correctness, security, regression — each
run on a DIFFERENT model family so a blind spot in one is caught by another. The
lens verdicts are fused worst-of into one recommendation and written to
~/.jarvis/auto-mods/<id>.review.json, which GET /api/evolution surfaces so the
reviewer sees the council's read BEFORE deciding to deploy.

ADVISORY ONLY. It never gates or blocks a deploy automatically — a human still
approves every deploy; this only informs that decision. Best-effort: a lens
whose model/key is unavailable falls back to the default Claude model, then to a
'skipped' verdict (NOT a pass). Never breaks finalize. Off the turn path — runs
in the build subprocess after a pending artifact is written, or on demand via
bin/jarvis-evolution-review.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pipeline.automod._state import _automod_home
from pipeline.automod.introspection import _parse_json_object

logger = logging.getLogger("jarvis.automod.review")

# Per-lens models — DIFFERENT families on purpose (the point of a council).
# Override any lens with JARVIS_REVIEW_MODEL_<LENS>="provider:model"
# (provider ∈ anthropic|openai|deepseek|groq; a bare value means anthropic).
# A lens falls back to the default Claude model if its provider/key/model is
# unavailable — a missing key degrades diversity, never the review.
_FALLBACK_MODEL = "claude-sonnet-4-6"
LENS_DEFAULTS: dict[str, str] = {
    # Gating lenses (can block) — 3 distinct families, the HIGHEST model of each.
    "correctness": "anthropic:claude-opus-4-8",
    "security": "openai:gpt-5.5",
    "regression": "deepseek:deepseek-reasoner",
    # Advisory lenses on distinct families too: the 6-lens council spans 6 model
    # providers, each the TOP available model (per Ulrich — quality over cost; the
    # council runs only on reviewable proposals). Model IDs researched + verified
    # live 2026-06-26 (gpt-5.5 / gemini-3.1-pro-preview / kimi-k2.7-code are the
    # API ceilings — the -pro/-research/-thinking variants 404). Override via
    # JARVIS_REVIEW_MODEL_<LENS>; a provider failure falls back to Claude.
    "expansionist": "gemini:gemini-3.1-pro-preview",
    "researcher": "kimi:kimi-k2.7-code",
    "role_player": "groq:openai/gpt-oss-120b",
}
# OpenAI-compatible providers (base_url + key env) reachable via the openai SDK.
_OPENAI_COMPAT: dict[str, dict] = {
    "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "kimi": {"base_url": "https://api.moonshot.ai/v1", "key_env": "KIMI_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GOOGLE_API_KEY"},
}
_DIFF_CAP = 24000  # bound the prompt; proposals are capped at <=5 files / 2000 diff lines

# verdict severity ordering — fusion takes the worst across lenses.
_SEVERITY = {"pass": 0, "concern": 1, "block": 2}

LENSES: dict[str, str] = {
    "correctness": (
        "You are the CORRECTNESS reviewer. Decide whether this diff correctly "
        "implements its stated intent. Hunt for logic bugs, inverted or wrong "
        "conditions, off-by-one errors, missed edge cases, broken control flow, "
        "wrong types, and code that does not actually do what the intent claims. "
        "Ignore style and security — other reviewers cover those."
    ),
    "security": (
        "You are the SECURITY reviewer. Hunt for security issues introduced by "
        "this diff: command/shell injection, path traversal, unsafe "
        "deserialization or eval/exec, hard-coded secrets, unsafe file writes, "
        "auth or permission bypass, SSRF, or weakened input validation. A diff "
        "with no security-relevant change is a 'pass'."
    ),
    "regression": (
        "You are the REGRESSION reviewer. Decide whether this diff risks breaking "
        "EXISTING behavior. Flag: removed or weakened tests/guards/assertions, "
        "changed function signatures or return contracts that other call sites "
        "depend on, altered default values, and behavior changes with no covering "
        "test. Purely additive code with its own tests is low risk."
    ),
}

# Advisory lenses — your spec's Expansionist / Researcher / Role-player. They
# ENRICH the review with perspectives the flaw-hunters above miss, but they NEVER
# gate the verdict: only the 3 LENSES above (the Contrarian / Principles cluster)
# can block. Their findings surface to the human in <id>.review.json under
# "advisory". On by default; JARVIS_AUTOMOD_REVIEW_ADVISORY=0 to skip (saves the
# extra model calls).
ADVISORY_LENSES: dict[str, str] = {
    "expansionist": (
        "You are the EXPANSIONIST reviewer. The change may be correct but small. "
        "Name the SINGLE biggest higher-leverage improvement the same effort could "
        "have made toward the intent, and whether a materially better approach "
        "exists. You NEVER block — you advise. If the scope is already right, say so."
    ),
    "researcher": (
        "You are the RESEARCHER reviewer. Judge the change against KNOWN industry "
        "practice and the relevant library/API idioms. Flag where it reinvents or "
        "contradicts established practice and name the authoritative approach. You "
        "NEVER block — you advise."
    ),
    "role_player": (
        "You are the ROLE-PLAYER reviewer. Stand in the shoes of the user / "
        "operator / tester who lives with this change. Is it correct but awkward, "
        "surprising, or bad to actually use or operate? Flag experience problems a "
        "code review misses. You NEVER block — you advise."
    ),
}

_RUBRIC = (
    "Respond with ONLY a JSON object, no prose:\n"
    '{"verdict": "pass" | "concern" | "block", '
    '"findings": ["<specific; cite file:line where you can>", ...], '
    '"summary": "<one sentence>"}\n'
    "verdict guide: pass = nothing wrong in YOUR lens; concern = worth a human "
    "look but not necessarily blocking; block = a real defect that should stop "
    "the deploy. Keep findings concrete and few (max 5)."
)

# System instruction — Claude otherwise prepends a long step-by-step analysis
# before the JSON (live 2026-06-25: that made the correctness lens unparseable /
# truncated). A system turn forces JSON-only; applied to every provider.
_SYSTEM = (
    "You are a precise code reviewer. Respond with ONLY the JSON object the user "
    "specifies — no preamble, no step-by-step analysis, no prose, no markdown fences."
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _review_path(automod_id: str) -> Path:
    return _automod_home() / f"{automod_id}.review.json"


def _skipped(reason: str) -> dict:
    return {"verdict": "skipped", "findings": [], "summary": reason}


def _lens_spec(lens: str) -> tuple[str, str]:
    """(provider, model) for a lens — env-overridable; a bare value = anthropic."""
    raw = os.environ.get(
        f"JARVIS_REVIEW_MODEL_{lens.upper()}",
        LENS_DEFAULTS.get(lens, f"anthropic:{_FALLBACK_MODEL}"),
    )
    provider, sep, model = raw.partition(":")
    if not sep:  # bare model name → anthropic (back-compat)
        return "anthropic", provider
    return provider, model


def _any_provider_key() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or any(os.environ.get(c["key_env"]) for c in _OPENAI_COMPAT.values())
    )


def _call_model(provider: str, model: str, prompt: str) -> str:
    """One text completion from any provider. Anthropic via its SDK; everything
    else via the openai SDK against the provider's base_url. Raises on failure."""
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(timeout=45.0, max_retries=1)
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content).strip()

    cfg = _OPENAI_COMPAT.get(provider)
    if not cfg:
        raise RuntimeError(f"unknown provider {provider!r}")
    key = os.environ.get(cfg["key_env"])
    if not key:
        raise RuntimeError(f"no {cfg['key_env']}")
    import openai

    client = openai.OpenAI(base_url=cfg["base_url"], api_key=key, timeout=45.0, max_retries=1)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _review_one(lens: str, instruction: str, intent: str, diff: str) -> dict:
    """One structured pass for a single lens, on that lens's model with a
    fallback to the default Claude model. Best-effort → 'skipped' if every
    attempt fails; an unknown/missing verdict normalizes to 'concern' (never a
    silent pass). Records which model produced the verdict."""
    prompt = (
        f"{instruction}\n\n"
        f"PROPOSAL INTENT:\n{(intent or '')[:1500]}\n\n"
        f"UNIFIED DIFF (truncated to {_DIFF_CAP} chars):\n{(diff or '')[:_DIFF_CAP]}\n\n"
        f"{_RUBRIC}"
    )
    provider, model = _lens_spec(lens)
    attempts = [(provider, model)]
    if provider != "anthropic":
        attempts.append(("anthropic", _FALLBACK_MODEL))  # keep the lens working even if its model is down
    last = "no attempt"
    for ap, am in attempts:
        try:
            text = _call_model(ap, am, prompt)
        except Exception as e:  # noqa: BLE001 — advisory; try the fallback, then skip
            last = str(e)
            logger.warning("[review] %s lens (%s:%s) failed: %s", lens, ap, am, e)
            continue
        parsed = _parse_json_object(text)
        if not parsed:
            last = "unparseable model output"
            continue
        verdict = str(parsed.get("verdict", "")).lower().strip()
        if verdict not in _SEVERITY:
            verdict = "concern"  # unknown → flag for a human; never silently pass
        findings = [str(f) for f in (parsed.get("findings") or [])][:5]
        return {
            "verdict": verdict,
            "findings": findings,
            "summary": str(parsed.get("summary", "")).strip(),
            "model": f"{ap}:{am}",
        }
    return {**_skipped(last), "model": f"{provider}:{model}"}


def _fuse(lenses: dict[str, dict]) -> dict:
    """Worst-of fusion → overall verdict + recommendation. 'skipped' lenses do
    NOT count toward severity but ARE recorded, so a skipped lens never reads as
    a pass. If every lens skipped, the overall is 'skipped' (recommend review)."""
    scored = [(l, _SEVERITY[v["verdict"]]) for l, v in lenses.items() if v["verdict"] in _SEVERITY]
    skipped = sorted(l for l, v in lenses.items() if v["verdict"] == "skipped")
    if not scored:
        return {"verdict": "skipped", "recommendation": "review",
                "blocking_lenses": [], "concern_lenses": [], "skipped": skipped}
    worst = max(s for _, s in scored)
    verdict = {0: "pass", 1: "concern", 2: "block"}[worst]
    recommendation = {"pass": "approve", "concern": "caution", "block": "reject"}[verdict]
    return {
        "verdict": verdict,
        "recommendation": recommendation,
        "blocking_lenses": sorted(l for l, s in scored if s == 2),
        "concern_lenses": sorted(l for l, s in scored if s == 1),
        "skipped": skipped,
    }


def _write(automod_id: str, review: dict) -> None:
    try:
        p = _review_path(automod_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("[review] write failed for %s: %s", automod_id, e)


def _models_map() -> dict[str, str]:
    return {l: f"{_lens_spec(l)[0]}:{_lens_spec(l)[1]}" for l in LENSES}


def _all_skipped_review(automod_id: str, reason: str) -> dict:
    review = {
        "automod_id": automod_id,
        "models": _models_map(),
        "generated_at": _now_iso(),
        "overall": {"verdict": "skipped", "recommendation": "review",
                    "blocking_lenses": [], "concern_lenses": [], "skipped": sorted(LENSES)},
        "lenses": {l: {**_skipped(reason), "model": f"{_lens_spec(l)[0]}:{_lens_spec(l)[1]}"} for l in LENSES},
    }
    _write(automod_id, review)
    return review


def review_proposal(automod_id: str, diff: str, intent: str) -> dict:
    """Run the 3-lens council on one proposal; persist + return the review.

    ADVISORY: writes <id>.review.json but never changes the proposal's status —
    a human still approves. Best-effort; never raises."""
    if not _any_provider_key():
        return _all_skipped_review(automod_id, "no provider API key")
    if not (diff or "").strip():
        return _all_skipped_review(automod_id, "no diff to review")

    lenses = {l: _review_one(l, instr, intent, diff) for l, instr in LENSES.items()}
    # Advisory lenses enrich the review but NEVER feed _fuse (gating stays the 3
    # proven lenses). On by default; disable with JARVIS_AUTOMOD_REVIEW_ADVISORY=0.
    advisory: dict[str, dict] = {}
    if os.environ.get("JARVIS_AUTOMOD_REVIEW_ADVISORY", "1") != "0":
        advisory = {l: _review_one(l, instr, intent, diff) for l, instr in ADVISORY_LENSES.items()}
    review = {
        "automod_id": automod_id,
        "models": {l: lenses[l].get("model", "") for l in LENSES},
        "generated_at": _now_iso(),
        "overall": _fuse(lenses),
        "lenses": lenses,
        "advisory": advisory,
    }
    _write(automod_id, review)
    try:
        from pipeline.automod import artifact

        artifact.audit("automod_reviewed", id=automod_id,
                       verdict=review["overall"]["verdict"],
                       recommendation=review["overall"]["recommendation"])
    except Exception:  # noqa: BLE001 — audit must never break the review
        pass
    return review


def council_blocks(review: dict) -> bool:
    """Whether the council's gating verdict should route the proposal back to
    rework (instead of leaving it reviewable). GATED by
    JARVIS_AUTOMOD_COUNCIL_GATES=1 — default OFF, i.e. the council stays advisory
    and a human decides. Only the 3 gating lenses feed the verdict, so this never
    fires on an advisory-lens concern."""
    if os.environ.get("JARVIS_AUTOMOD_COUNCIL_GATES") != "1":
        return False
    return review.get("overall", {}).get("verdict") == "block"


def read_review(automod_id: str) -> dict | None:
    """Return the last stored review for an id, or None."""
    try:
        return json.loads(_review_path(automod_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pending_ids() -> list[str]:
    """Ids of all proposals with status 'pending' (built, reviewable, awaiting a
    human decision)."""
    from pipeline.automod._state import _automod_home

    ids: list[str] = []
    for f in sorted(_automod_home().glob("automod-*.json")):
        if f.name.endswith(".review.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("status") == "pending":
            ids.append(str(d.get("id") or f.name[: -len(".json")]))
    return ids


def _review_all_status_path() -> Path:
    from pipeline.automod._state import _automod_home

    return _automod_home() / ".review-all-status.json"


def _write_review_all_status(status: dict) -> None:
    p = _review_all_status_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(status), encoding="utf-8")
    except OSError:
        pass


def review_all_pending(concurrency: int | None = None) -> dict:
    """Re-run the council on EVERY pending proposal, in parallel (bounded), so the
    backlog can be reviewed in one shot instead of one-at-a-time through the UI's
    single-action gate. Regenerates each <id>.review.json with the current council.
    Writes a .review-all-status.json the /evolution UI polls so verdicts + progress
    appear INCREMENTALLY (not all-at-once). Best-effort per proposal."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from pipeline.automod._state import artifact_path

    if concurrency is None:
        try:
            concurrency = max(1, int(os.environ.get("JARVIS_AUTOMOD_REVIEW_CONCURRENCY", "4")))
        except ValueError:
            concurrency = 4

    ids = _pending_ids()
    total = len(ids)
    started = _now_iso()
    lock = threading.Lock()
    progress = {"done": 0}
    _write_review_all_status({"running": total > 0, "total": total, "done": 0, "started_at": started})

    def _one(automod_id: str) -> dict:
        try:
            art = json.loads(artifact_path(automod_id).read_text(encoding="utf-8"))
            rev = review_proposal(automod_id, str(art.get("diff", "")), str(art.get("intent", "")))
            r = {"id": automod_id, "ok": True, "verdict": (rev.get("overall") or {}).get("verdict", "?")}
        except Exception as exc:  # noqa: BLE001 — one bad proposal must not abort the batch
            r = {"id": automod_id, "ok": False, "error": str(exc)}
        with lock:
            progress["done"] += 1
            _write_review_all_status({"running": progress["done"] < total, "total": total,
                                      "done": progress["done"], "started_at": started})
        return r

    results: list[dict] = []
    if ids:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(_one, ids))
    _write_review_all_status({"running": False, "total": total, "done": total,
                              "started_at": started, "finished_at": _now_iso()})
    return {
        "count": total,
        "reviewed": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "concurrency": concurrency,
        "results": results,
    }


def _main(argv: list[str]) -> int:
    """CLI: `python -m pipeline.automod.review_council <automod_id> | --all` —
    reviews one proposal (by reading the diff in its artifact) or, with --all,
    every pending proposal in parallel. Used by bin/jarvis-evolution-review."""
    if not argv:
        print(json.dumps({"error": "usage: review_council <automod_id> | --all"}))
        return 2
    if argv[0] in ("--all", "-a"):
        print(json.dumps(review_all_pending(), indent=2, ensure_ascii=False))
        return 0
    automod_id = argv[0]
    from pipeline.automod._state import artifact_path

    try:
        art = json.loads(artifact_path(automod_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"cannot read artifact: {e}"}))
        return 1
    review = review_proposal(automod_id, str(art.get("diff", "")), str(art.get("intent", "")))
    print(json.dumps(review, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))

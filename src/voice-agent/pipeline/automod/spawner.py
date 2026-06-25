"""Async subprocess spawner for auto-mod intents (Spec B, Plane 3).

drain_queue() reads ~/.jarvis/auto-mods/queue.jsonl, gates each entry
through throttle.admit_intent(), and on admit launches
`bin/jarvis-automod-impl <intent_file>` via
asyncio.create_subprocess_exec.

Lockfile (fcntl.flock exclusive) serializes spawns globally -- at most
one auto-mod subprocess runs at a time. Per-topic in-flight cap = 1
is naturally enforced by this.

Timeout: SPAWN_TIMEOUT_S (10 min). Belt + suspenders with the shell
wrapper's own `timeout 600`.

Gated by JARVIS_AUTOMOD_SPAWN_LIVE=1. When unset, drain_queue() is a
no-op (queue intact, intents accumulate for later inspection).

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
from pathlib import Path

from pipeline.automod import artifact, criteria, throttle
from pipeline.automod._state import (
    _automod_home,
    intent_file_path,
    lockfile_path,
    queue_path,
)

logger = logging.getLogger("jarvis.automod.spawner")

# 30 min by default: a build must explore + edit + run the full test suite
# (~70s) + commit, which 10 min couldn't fit (every build hit
# automod_spawn_timeout → no_commit_landed). Must be >= the wrapper's inner
# `timeout` so finalize gets to run. Env-overridable.
SPAWN_TIMEOUT_S = int(os.environ.get("JARVIS_AUTOMOD_SPAWN_TIMEOUT_S", "1800"))

# Repo root + absolute path to the wrapper script. This file lives at
# .../src/voice-agent/pipeline/automod/spawner.py, so:
#   parents[0] = automod/
#   parents[1] = pipeline/
#   parents[2] = voice-agent/
#   parents[3] = src/
#   parents[4] = repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
WRAPPER_SCRIPT = REPO_ROOT / "bin" / "jarvis-automod-impl"


def _spawn_live() -> bool:
    return os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0") == "1"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _worktree_path(rec_id: str) -> Path:
    # MUST live at ~/.jarvis/worktrees/<id>, NOT ~/.jarvis/auto-mods/worktrees/<id>.
    # The CLI's auto-edit guard treats any '.jarvis' path segment as a dangerous
    # directory and refuses edits, EXCEPT when the segment right after '.jarvis'
    # is exactly 'worktrees' (its git-worktree exemption). Nesting under
    # auto-mods/ misses that exemption, so the build agent can't write a single
    # file in the worktree → every build failed no_commit_landed (2026-06-23).
    return _automod_home().parent / "worktrees" / rec_id


def _prepare_worktree(rec_id: str) -> tuple[Path, str]:
    """Create a detached, disposable worktree for one proposal. Returns
    (worktree_path, base_sha).

    The wrapper used to reset the live checkout to origin/master before
    branching. That is unsafe when the user's local master is ahead of origin.
    A throwaway worktree gives the coding subprocess a clean base without ever
    moving the live checkout.

    base_sha is the EXACT commit the worktree was created from, captured here so
    the wrapper + finalize pin every git op (checkout, diff) to that snapshot.
    Without this, finalize diffed against the live `master` ref — and a parallel
    session committing to master mid-build polluted the proposal's diff with
    unrelated files (live 2026-06-23: a concurrent cloudflare commit made a
    1-file docstring proposal look like 6 files → bogus too_many_files reject).
    """
    wt = _worktree_path(rec_id)
    if wt.exists():
        _git("worktree", "remove", "--force", str(wt))
    wt.parent.mkdir(parents=True, exist_ok=True)

    # Base the disposable worktree on the CURRENT code. Default = local `master`
    # (origin/master is intentionally stale here — local is 32 commits ahead, and
    # building from stale origin both misses current features and explodes the
    # diff). Override with JARVIS_AUTOMOD_BASE_REF.
    base = os.environ.get("JARVIS_AUTOMOD_BASE_REF", "master")
    if base.startswith("origin/"):
        # only fetch when explicitly building against the remote
        _git("fetch", "origin", "master", "--quiet")

    add = _git("worktree", "add", "--detach", "--force", str(wt), base)
    if add.returncode != 0:
        raise RuntimeError(f"git worktree add failed (base={base}): {add.stderr.strip()}")
    # Pin to the resolved commit, NOT the moving `base` ref.
    rev = _git("-C", str(wt), "rev-parse", "HEAD")
    base_sha = rev.stdout.strip() or base
    return wt, base_sha


def _cleanup_worktree(wt: Path) -> None:
    rm = _git("worktree", "remove", "--force", str(wt))
    if rm.returncode != 0:
        logger.warning("[automod] worktree cleanup failed: %s", rm.stderr.strip())


def _branch_name(rec_id: str) -> str:
    return f"automod/{rec_id}"


def _already_built(rec_id: str) -> bool:
    """True when rec_id already produced a LANDABLE proposal: pending (awaiting
    review) or merged (deployed). Such an id must never be rebuilt — its commit
    lives on automod/<id> and rebuilding would wipe it."""
    try:
        return artifact.load(rec_id).get("status") in ("pending", "merged")
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _delete_stale_branch(rec_id: str) -> None:
    """Force-delete a leftover automod/<id> branch if it exists (no-op when it
    doesn't). -D (not -d) because a failed build's branch may carry an un-merged
    commit we are intentionally discarding.

    Why this is needed: finalize deletes a failed branch via
    `git checkout master; git branch -D` run INSIDE the disposable worktree,
    where git refuses the master checkout (master is checked out in the main
    tree), so its branch delete silently no-ops. The branch is then left behind
    and the next build of that id dies `fatal: branch already exists`."""
    branch = _branch_name(rec_id)
    if _git("rev-parse", "--verify", "--quiet", branch).returncode != 0:
        return  # branch doesn't exist — nothing to clean
    rm = _git("branch", "-D", branch)
    if rm.returncode != 0:
        logger.warning("[automod] stale branch delete failed: %s — %s",
                       branch, rm.stderr.strip())


def prune_orphan_branches() -> int:
    """Delete automod/* branches that hold no landable proposal — i.e. whose
    artifact is failed/rejected/absent. KEEPS pending (awaiting review) + merged
    (deployed; the branch is the rollback handle) branches, and any branch
    checked out in a worktree (an in-flight build). Returns the count deleted.

    Companion to `_delete_stale_branch` (which cleans the one id being
    re-spawned): this sweeps the rest. Failed-build branches piled up (15+ by
    2026-06-23) because finalize's in-worktree delete silently no-ops; wired
    into the nightly pass so they can't accumulate into 'branch already exists'
    collisions again. Always-safe; never raises (logs + returns count)."""
    listed = _git("branch", "--list", "automod/*", "--format=%(refname:short)")
    branches = [b.strip() for b in listed.stdout.splitlines() if b.strip()]
    if not branches:
        return 0
    # Branches checked out in a worktree (an in-flight build) must not be touched.
    checked_out: set[str] = set()
    for line in _git("worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("branch "):
            checked_out.add(line[len("branch "):].strip().replace("refs/heads/", ""))
    deleted = 0
    for branch in branches:
        if branch in checked_out:
            continue
        rec_id = branch[len("automod/"):]
        try:
            status = artifact.load(rec_id).get("status")
        except (FileNotFoundError, json.JSONDecodeError):
            status = None
        if status in ("pending", "merged"):
            continue  # landable / deployed — keep the branch
        if _git("branch", "-D", branch).returncode == 0:
            deleted += 1
    if deleted:
        logger.info("[automod] pruned %d orphan automod branch(es)", deleted)
        artifact.audit("automod_branches_pruned", count=deleted)
    return deleted


@contextlib.contextmanager
def _global_lock():
    """Exclusive lockfile via fcntl.flock -- at most one spawn at a time."""
    p = lockfile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: automod is single-process + gated; skip lock
        yield
        return
    fd = open(p, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        fd.close()


def _read_queue() -> list[dict]:
    p = queue_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("[automod] dropped malformed queue entry: %r", line[:120])
    return out


def _truncate_queue() -> None:
    """Drain queue.jsonl after processing (no retries)."""
    p = queue_path()
    if p.exists():
        p.write_text("", encoding="utf-8")


def _write_queue(records: list[dict]) -> None:
    p = queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    p.write_text(body, encoding="utf-8")


async def _spawn_one(intent: dict) -> str:
    """Launch the wrapper script for a single intent. Returns
    'spawned' / 'skipped' / 'timeout' / 'error'."""
    intent = criteria.enrich_record(intent)
    rec_id = intent["id"]

    # Never rebuild an id that already produced a landable proposal. A PENDING
    # artifact's commit lives on automod/<id> awaiting review; a MERGED one is
    # deployed. Re-spawning wipes that branch, burns a build slot, and — because
    # the branch still exists — makes the wrapper's `git checkout -b` die
    # "branch already exists" (live 2026-06-25: the cycle re-spawned an
    # already-built 59b830 → fatal). 'skipped' drains the queue entry without
    # counting it as a build.
    if _already_built(rec_id):
        logger.info("[automod] skip rebuild — id=%s already has a landable proposal", rec_id)
        artifact.audit("automod_skip_already_built", id=rec_id)
        return "skipped"

    # A prior FAILED/abandoned build can leave automod/<id> behind (finalize's
    # in-worktree branch delete silently no-ops — see _delete_stale_branch).
    # Clear it from the main repo before the wrapper's `checkout -b`. Safe: we
    # only reach here when no pending/merged artifact exists for this id.
    _delete_stale_branch(rec_id)

    intent_file = intent_file_path(rec_id)
    intent_file.parent.mkdir(parents=True, exist_ok=True)
    evolution_json = json.dumps(intent.get("evolution", {}), ensure_ascii=False)
    prior_json = json.dumps(intent.get("prior_failures", []), ensure_ascii=False)
    # Guard: 'intent' is required. A queue entry that passed JSON decode but
    # omits the key would cause an uncaught KeyError here (outside the
    # try/except below), crashing the full drain loop and losing remaining
    # queue entries. Treat it as a malformed entry and return 'error' cleanly.
    intent_text = intent.get("intent")
    if not intent_text:
        logger.warning("[automod] malformed queue entry — missing 'intent': id=%s", rec_id)
        artifact.audit("automod_spawn_error", id=rec_id, error="missing intent field")
        return "error"
    # Retry-lineage fields first (single-line, parsed by finalize._read_intent);
    # INTENT last because it may be multi-line (retry bodies are). The wrapper
    # cat's the whole file, so order doesn't affect the coding-agent prompt.
    intent_file.write_text(
        f"ATTEMPT: {intent.get('attempt', 1)}\n"
        f"LINEAGE: {intent.get('lineage', rec_id)}\n"
        f"PRIOR_FAILURES: {prior_json}\n"
        f"PRIORITY: {intent.get('priority', 'P3')}\n"
        f"KIND: {intent.get('kind', 'unknown')}\n"
        f"RATIONALE: {intent.get('rationale', '')}\n"
        f"EVOLUTION: {evolution_json}\n"
        f"INTENT: {intent_text}\n",
        encoding="utf-8",
    )

    if not WRAPPER_SCRIPT.exists():
        logger.error("[automod] wrapper missing: %s", WRAPPER_SCRIPT)
        artifact.audit("automod_spawn_error", id=rec_id,
                       error="wrapper script missing")
        return "error"

    try:
        worktree, base_sha = _prepare_worktree(rec_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[automod] worktree prep failed: id=%s err=%s", rec_id, e)
        artifact.audit("automod_spawn_error", id=rec_id,
                       error=f"worktree prep failed: {e}")
        return "error"

    artifact.audit("automod_spawning", id=rec_id, intent_kind=intent.get("kind"))
    logger.info("[automod] spawning: id=%s timeout=%ss",
                rec_id, SPAWN_TIMEOUT_S)

    try:
        env = os.environ.copy()
        env["JARVIS_AUTOMOD_REPO_ROOT"] = str(worktree)
        env["JARVIS_AUTOMOD_TOOLING_ROOT"] = str(REPO_ROOT)
        # Pin the wrapper checkout + finalize diff to the EXACT commit the
        # worktree was created from (base_sha), NOT the live `master` ref. A
        # parallel session committing to master mid-build was moving `master`
        # out from under finalize, so finalize diffed against the wrong base
        # and swept unrelated files into the proposal (live 2026-06-23: a
        # concurrent cloudflare commit turned a 1-file docstring into a bogus
        # too_many_files:6>5 reject). The SHA can't move.
        env["JARVIS_AUTOMOD_BASE_REF"] = base_sha
        env["JARVIS_AUTOMOD_SKIP_BASE_FETCH"] = "1"
        proc = await asyncio.create_subprocess_exec(
            str(WRAPPER_SCRIPT),
            str(intent_file),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        await asyncio.wait_for(proc.wait(), timeout=SPAWN_TIMEOUT_S)
        artifact.audit("automod_spawn_complete", id=rec_id,
                       exit_code=proc.returncode)
        return "spawned"
    except asyncio.TimeoutError:
        logger.warning("[automod] spawn timed out: id=%s", rec_id)
        artifact.audit("automod_spawn_timeout", id=rec_id)
        return "timeout"
    except Exception as e:  # noqa: BLE001
        logger.warning("[automod] spawn error: id=%s err=%s", rec_id, e)
        artifact.audit("automod_spawn_error", id=rec_id, error=str(e))
        return "error"
    finally:
        _cleanup_worktree(worktree)


async def drain_queue(*, only_id: str | None = None, force: bool = False) -> int:
    """Drain queue.jsonl: for each intent, gate via throttle; on admit,
    spawn the wrapper. Returns count of successfully launched spawns.

    `force=True` is reserved for admin/debug paths. Normal manual and auto
    evolution both leave force=False so the 5/day budget bounds all builds.

    Always-safe. No-op when JARVIS_AUTOMOD_SPAWN_LIVE != '1' or when the
    evolution cycle is paused."""
    if not _spawn_live():
        logger.debug("[automod] spawn disabled (shadow mode)")
        return 0
    from pipeline.automod._state import is_evolution_paused
    if is_evolution_paused():
        logger.info("[automod] evolution paused — drain_queue is a no-op")
        return 0

    queue = _read_queue()
    if not queue:
        return 0

    spawned = 0
    remaining: list[dict] = []
    with _global_lock():
        for intent in queue:
            if only_id and intent.get("id") != only_id:
                remaining.append(intent)
                continue
            if not force:
                ok, reason = throttle.admit_intent(intent)
                if not ok:
                    logger.info("[automod] intent rejected by throttle: id=%s reason=%s",
                                intent.get("id"), reason)
                    artifact.audit("automod_rejected", id=intent.get("id"),
                                   reason=reason)
                    if reason == "daily_cap_reached":
                        remaining.append(intent)
                    continue
            status = await _spawn_one(intent)
            if status == "spawned":
                # The daily cap counts only REVIEWABLE proposals: finalize calls
                # throttle.mark_admitted when it writes a PENDING artifact. A
                # spawned-but-failed build (no commit / tests red / rejected diff)
                # must NOT consume the budget (user 2026-06-23).
                spawned += 1
            # Timeout/error are consumed (don't retry).
        if only_id or remaining:
            _write_queue(remaining)
        else:
            _truncate_queue()
    if spawned:
        logger.info("[automod] drain complete: spawned=%d", spawned)
    return spawned

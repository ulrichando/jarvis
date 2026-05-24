#!/usr/bin/env python3
"""JARVIS batch agent runner — offline parallel evaluation across many prompts.

Runs JARVIS's tool registry against a dataset of prompts in TEXT mode (no
voice, no LiveKit, no STT/TTS), records each conversation's trajectory +
tool usage statistics, and aggregates everything for offline analysis.

Use cases:
    - Regression-test prompt changes: did the new soul.md actually help?
    - Benchmark tool-selection quality across a curated prompt set.
    - Generate training data: prompt → trajectory pairs.

Adapted from the upstream batch_runner pattern, with the agent loop
re-implemented against the Anthropic SDK + JARVIS's tool registry
(``tools.registry.registry.all_entries()``) instead of the upstream's
voice-less ``AIAgent`` class.

Usage::

    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run

    # Resume an interrupted run
    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run --resume

    # Limit how many prompts to run
    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run --max_samples=50

Dataset format (JSONL, one entry per line)::

    {"prompt": "What time is it in Cameroon?"}
    {"prompt": "Open Amazon and search for shoes", "metadata": {"category": "browser"}}

Outputs (under ``~/.local/share/jarvis/batch/<run_name>/``):
    - ``batch_NNN.jsonl``    — one entry per processed prompt
    - ``trajectories.jsonl`` — all entries merged after the run completes
    - ``checkpoint.json``    — resume state
    - ``statistics.json``    — aggregated tool/turn stats
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default LLM — TASK route default in JARVIS dispatcher; lightweight + cheap.
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TURNS = 10
DEFAULT_BATCH_SIZE = 10
DEFAULT_NUM_WORKERS = 4

# Where batch runs live (mirrors JARVIS_HOME-style layout).
DEFAULT_OUTPUT_ROOT = Path.home() / ".local" / "share" / "jarvis" / "batch"


# ─────────────────────────────────────────────────────────────────────
# Minimal text-mode agent loop — Anthropic SDK + JARVIS registry tools
# ─────────────────────────────────────────────────────────────────────


def _entries_to_anthropic_tools(entries: list) -> List[Dict[str, Any]]:
    """Convert JARVIS ToolEntry objects to the Anthropic /v1/messages tool shape.

    JARVIS schema:    {"name": ..., "description": ..., "parameters": {...}}
    Anthropic shape:  {"name": ..., "description": ..., "input_schema": {...}}

    Also forces ``additionalProperties: false`` on every object node (mirror of
    sanitizers/anthropic_strict_schema.py — Anthropic rejects without it).
    """
    from sanitizers.anthropic_strict_schema import fix_schema

    tools = []
    for entry in entries:
        schema = entry.schema or {}
        params = schema.get("parameters") or {"type": "object", "properties": {}}
        fix_schema(params)
        tools.append({
            "name": entry.name,
            "description": entry.description
                or schema.get("description", f"Tool {entry.name}"),
            "input_schema": params,
        })
    return tools


def _resolve_handler(entries: list, name: str):
    """Return (handler, is_async) for a registered tool by name, or (None, False)."""
    for entry in entries:
        if entry.name == name:
            return entry.handler, entry.is_async
    return None, False


async def _dispatch_tool(entries: list, name: str, raw_input: dict) -> str:
    """Invoke a registered JARVIS tool; coerce result to str; never raise."""
    handler, is_async = _resolve_handler(entries, name)
    if handler is None:
        return f"Error: tool {name!r} is not registered or not available"
    try:
        args = raw_input if isinstance(raw_input, dict) else {}
        if is_async:
            result = await handler(args)
        else:
            result = handler(args)
            if asyncio.iscoroutine(result):
                result = await result
        if isinstance(result, str):
            return result
        if result is None:
            return ""
        return str(result)
    except Exception as exc:  # noqa: BLE001 — tool errors must not crash the loop
        return f"Error: {name} raised {type(exc).__name__}: {exc}"


async def _run_agent_loop(
    prompt: str,
    *,
    model: str,
    max_turns: int,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one prompt through the Anthropic agent loop with JARVIS tools.

    Returns:
        {"messages": [...], "completed": bool, "partial": bool, "turns": int,
         "api_calls": int}
    """
    from anthropic import AsyncAnthropic
    from tools._adapter import load_all_livekit_tools  # triggers discovery
    from tools.registry import registry

    # Force registry population (load_all_livekit_tools handles discovery +
    # check_fn filtering); we then pull entries back out for our own loop.
    load_all_livekit_tools()
    entries = [
        e for e in registry.all_entries()
        if e.check_fn is None or registry.is_available(e.name)
    ]
    anthropic_tools = _entries_to_anthropic_tools(entries)

    client = AsyncAnthropic()
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": prompt}]}
    ]

    api_calls = 0
    completed = False
    partial = False
    turn = 0

    while turn < max_turns:
        turn += 1
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "tools": anthropic_tools,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        try:
            response = await client.messages.create(**kwargs)
            api_calls += 1
        except Exception as exc:  # noqa: BLE001 — record + bail cleanly
            logger.warning("Anthropic API failed on turn %d: %s", turn, exc)
            partial = True
            break

        assistant_blocks = list(response.content)
        messages.append({"role": "assistant", "content": [
            block.model_dump() for block in assistant_blocks
        ]})

        # If the model finished with no tool_use, we're done.
        tool_uses = [b for b in assistant_blocks if b.type == "tool_use"]
        if not tool_uses:
            completed = response.stop_reason in ("end_turn", "stop_sequence")
            break

        # Dispatch each requested tool and append the results.
        tool_results = []
        for tu in tool_uses:
            output = await _dispatch_tool(entries, tu.name, tu.input or {})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": output,
            })
        messages.append({"role": "user", "content": tool_results})

    if not completed and turn >= max_turns:
        partial = True

    return {
        "messages": messages,
        "completed": completed,
        "partial": partial,
        "turns": turn,
        "api_calls": api_calls,
    }


# ─────────────────────────────────────────────────────────────────────
# Tool-stats extraction (from the trajectory)
# ─────────────────────────────────────────────────────────────────────


def _extract_tool_stats(messages: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Count tool calls + successes/failures from an Anthropic-shaped trajectory.

    Mirrors the upstream pattern: success = tool_result whose content doesn't
    start with 'Error:' / 'error:'. Best-effort; not authoritative for tools
    whose own success signal is embedded in JSON.
    """
    stats: Dict[str, Dict[str, int]] = {}
    # Map tool_use_id → tool name so we can match results back to calls.
    id_to_name: Dict[str, str] = {}

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if msg["role"] == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    tu_id = block.get("id", "")
                    if not name:
                        continue
                    stats.setdefault(name, {"count": 0, "success": 0, "failure": 0})
                    stats[name]["count"] += 1
                    id_to_name[tu_id] = name
        elif msg["role"] == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tu_id = block.get("tool_use_id", "")
                    out = block.get("content", "")
                    if isinstance(out, list):
                        out = "".join(
                            b.get("text", "") for b in out
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    name = id_to_name.get(tu_id)
                    if not name or name not in stats:
                        continue
                    text = str(out).strip().lower()
                    if not text or text.startswith("error:"):
                        stats[name]["failure"] += 1
                    else:
                        stats[name]["success"] += 1
    return stats


# ─────────────────────────────────────────────────────────────────────
# Worker — one prompt at a time inside a multiprocessing worker
# ─────────────────────────────────────────────────────────────────────


def _process_single_prompt(
    prompt_index: int,
    prompt_data: Dict[str, Any],
    batch_num: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Worker function: process one prompt, return trajectory + stats."""
    prompt = prompt_data.get("prompt", "").strip()
    if not prompt:
        return {
            "success": False,
            "prompt_index": prompt_index,
            "error": "empty prompt",
            "metadata": {"batch_num": batch_num},
        }

    try:
        result = asyncio.run(_run_agent_loop(
            prompt,
            model=config["model"],
            max_turns=config["max_turns"],
            system_prompt=config.get("system_prompt"),
        ))
        stats = _extract_tool_stats(result["messages"])
        return {
            "success": True,
            "prompt_index": prompt_index,
            "trajectory": result["messages"],
            "tool_stats": stats,
            "completed": result["completed"],
            "partial": result["partial"],
            "turns": result["turns"],
            "api_calls": result["api_calls"],
            "metadata": {
                "batch_num": batch_num,
                "timestamp": datetime.now().isoformat(),
                "model": config["model"],
            },
        }
    except Exception as exc:  # noqa: BLE001 — log + continue, don't crash the pool
        if config.get("verbose"):
            traceback.print_exc()
        return {
            "success": False,
            "prompt_index": prompt_index,
            "error": str(exc),
            "metadata": {"batch_num": batch_num, "timestamp": datetime.now().isoformat()},
        }


def _process_batch_worker(args: Tuple) -> Dict[str, Any]:
    """Pool worker: process one batch of prompts; append trajectories to file."""
    batch_num, batch_data, output_dir, completed_set, config = args
    output_dir = Path(output_dir)
    batch_file = output_dir / f"batch_{batch_num:04d}.jsonl"

    to_process = [(idx, data) for idx, data in batch_data if idx not in completed_set]
    if not to_process:
        return {
            "batch_num": batch_num,
            "processed": 0,
            "skipped": len(batch_data),
            "tool_stats": {},
            "completed_prompts": [],
        }

    batch_tool_stats: Dict[str, Dict[str, int]] = {}
    completed_in_batch: List[int] = []

    for prompt_index, prompt_data in to_process:
        result = _process_single_prompt(prompt_index, prompt_data, batch_num, config)
        if result["success"] and result.get("trajectory"):
            with open(batch_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "prompt_index": prompt_index,
                    "prompt": prompt_data.get("prompt", ""),
                    "trajectory": result["trajectory"],
                    "tool_stats": result["tool_stats"],
                    "completed": result["completed"],
                    "partial": result["partial"],
                    "turns": result["turns"],
                    "api_calls": result["api_calls"],
                    "metadata": result["metadata"],
                }, ensure_ascii=False) + "\n")
            completed_in_batch.append(prompt_index)
            for name, s in result["tool_stats"].items():
                slot = batch_tool_stats.setdefault(name, {"count": 0, "success": 0, "failure": 0})
                slot["count"] += s["count"]
                slot["success"] += s["success"]
                slot["failure"] += s["failure"]
        elif not result["success"]:
            with open(batch_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "prompt_index": prompt_index,
                    "prompt": prompt_data.get("prompt", ""),
                    "failed": True,
                    "error": result.get("error"),
                    "metadata": result["metadata"],
                }, ensure_ascii=False) + "\n")

    return {
        "batch_num": batch_num,
        "processed": len(to_process),
        "skipped": len(batch_data) - len(to_process),
        "tool_stats": batch_tool_stats,
        "completed_prompts": completed_in_batch,
    }


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────


class BatchRunner:
    """Loads a dataset, splits it into batches, processes them in parallel."""

    def __init__(
        self,
        dataset_file: Path,
        run_name: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        num_workers: int = DEFAULT_NUM_WORKERS,
        model: str = DEFAULT_MODEL,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_samples: Optional[int] = None,
        system_prompt: Optional[str] = None,
        verbose: bool = False,
        output_root: Path = DEFAULT_OUTPUT_ROOT,
    ) -> None:
        self.dataset_file = Path(dataset_file)
        self.run_name = run_name
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.model = model
        self.max_turns = max_turns
        self.max_samples = max_samples
        self.system_prompt = system_prompt
        self.verbose = verbose

        self.output_dir = output_root / run_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.output_dir / "checkpoint.json"
        self.stats_file = self.output_dir / "statistics.json"

        self.dataset = self._load_dataset()
        if max_samples and max_samples < len(self.dataset):
            self.dataset = self.dataset[:max_samples]
        self.batches = self._create_batches()

        print(f"📊 BatchRunner ready")
        print(f"   dataset:  {self.dataset_file} ({len(self.dataset)} prompts)")
        print(f"   batches:  {len(self.batches)} (batch_size={self.batch_size})")
        print(f"   workers:  {self.num_workers}")
        print(f"   model:    {self.model}  max_turns={self.max_turns}")
        print(f"   output:   {self.output_dir}")

    # ── Dataset / batches ──────────────────────────────────────────
    def _load_dataset(self) -> List[Dict[str, Any]]:
        if not self.dataset_file.exists():
            raise FileNotFoundError(self.dataset_file)
        dataset: List[Dict[str, Any]] = []
        with open(self.dataset_file, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"⚠️  line {ln}: invalid JSON ({e})", file=sys.stderr)
                    continue
                if "prompt" not in entry:
                    print(f"⚠️  line {ln}: missing 'prompt' field, skipping", file=sys.stderr)
                    continue
                dataset.append(entry)
        if not dataset:
            raise ValueError(f"no valid entries in {self.dataset_file}")
        return dataset

    def _create_batches(self) -> List[List[Tuple[int, Dict[str, Any]]]]:
        return [
            [(idx, entry) for idx, entry in enumerate(self.dataset[i:i + self.batch_size], start=i)]
            for i in range(0, len(self.dataset), self.batch_size)
        ]

    # ── Checkpointing ───────────────────────────────────────────────
    def _load_checkpoint(self) -> Dict[str, Any]:
        if not self.checkpoint_file.exists():
            return {"run_name": self.run_name, "completed_prompts": [], "batch_stats": {}}
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  checkpoint load failed ({e}); starting fresh", file=sys.stderr)
            return {"run_name": self.run_name, "completed_prompts": [], "batch_stats": {}}

    def _save_checkpoint(self, data: Dict[str, Any]) -> None:
        data["last_updated"] = datetime.now().isoformat()
        tmp = self.checkpoint_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.checkpoint_file)

    # ── Run ─────────────────────────────────────────────────────────
    def run(self, resume: bool = False) -> None:
        checkpoint = self._load_checkpoint() if resume else {
            "run_name": self.run_name, "completed_prompts": [], "batch_stats": {},
        }
        completed_set = set(checkpoint.get("completed_prompts", []))
        if resume and completed_set:
            print(f"🔁 resume: {len(completed_set)} prompts already completed")

        config = {
            "model": self.model,
            "max_turns": self.max_turns,
            "system_prompt": self.system_prompt,
            "verbose": self.verbose,
        }
        tasks = [
            (batch_num, batch_data, str(self.output_dir), completed_set, config)
            for batch_num, batch_data in enumerate(self.batches)
        ]

        total_tool_stats: Dict[str, Dict[str, int]] = {}
        start = time.time()

        print(f"🚀 starting {self.num_workers} workers on {len(tasks)} batches\n")
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None  # type: ignore[assignment]

        results = []
        with Pool(processes=self.num_workers) as pool:
            iterator = pool.imap_unordered(_process_batch_worker, tasks)
            wrapped = tqdm(iterator, total=len(tasks), desc="batches") if tqdm else iterator
            for result in wrapped:
                results.append(result)
                completed_set.update(result.get("completed_prompts", []))
                bn = result.get("batch_num")
                if isinstance(bn, int):
                    checkpoint.setdefault("batch_stats", {})[str(bn)] = {
                        "processed": result.get("processed", 0),
                        "skipped": result.get("skipped", 0),
                    }
                checkpoint["completed_prompts"] = sorted(completed_set)
                try:
                    self._save_checkpoint(checkpoint)
                except Exception as ce:  # noqa: BLE001
                    print(f"⚠️  checkpoint save failed: {ce}", file=sys.stderr)

        # Aggregate tool stats across all batches.
        for batch_result in results:
            for name, s in batch_result.get("tool_stats", {}).items():
                slot = total_tool_stats.setdefault(name, {"count": 0, "success": 0, "failure": 0})
                slot["count"] += s["count"]
                slot["success"] += s["success"]
                slot["failure"] += s["failure"]
        for name, s in total_tool_stats.items():
            total = s["success"] + s["failure"]
            s["success_rate"] = round(100 * s["success"] / total, 2) if total else 0.0

        # Combine all batch files into one trajectories.jsonl.
        combined = self.output_dir / "trajectories.jsonl"
        entries_written = 0
        with open(combined, "w", encoding="utf-8") as out:
            for bf in sorted(self.output_dir.glob("batch_*.jsonl")):
                with open(bf, "r", encoding="utf-8") as inf:
                    for line in inf:
                        out.write(line)
                        entries_written += 1
        print(f"\n📦 combined {entries_written} entries → {combined}")

        # Stats file.
        final = {
            "run_name": self.run_name,
            "model": self.model,
            "max_turns": self.max_turns,
            "total_prompts": len(self.dataset),
            "total_batches": len(self.batches),
            "batch_size": self.batch_size,
            "completed_at": datetime.now().isoformat(),
            "duration_seconds": round(time.time() - start, 2),
            "tool_statistics": total_tool_stats,
        }
        with open(self.stats_file, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)

        # Pretty summary.
        print("\n" + "=" * 70)
        print("📊 BATCH PROCESSING COMPLETE")
        print("=" * 70)
        print(f"✅ processed this run: {sum(r.get('processed', 0) for r in results)}")
        print(f"⏱️  duration: {round(time.time() - start, 2)}s")
        if total_tool_stats:
            print(f"\n📈 Tool Usage:")
            print(f"{'Tool':<25} {'Count':<8} {'Success':<10} {'Failure':<10} {'Success %':<10}")
            for name, s in sorted(total_tool_stats.items(), key=lambda kv: -kv[1]["count"]):
                print(f"{name:<25} {s['count']:<8} {s['success']:<10} {s['failure']:<10} "
                      f"{s.get('success_rate', 0):.1f}%")
        print(f"\n💾 outputs:")
        print(f"   trajectories: {combined}")
        print(f"   stats:        {self.stats_file}")
        print(f"   checkpoint:   {self.checkpoint_file}")


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batch_runner",
        description="Run JARVIS in text mode across a dataset of prompts; "
                    "record trajectories + tool statistics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--dataset_file", required=True, type=Path,
                   help="JSONL file with one {'prompt': '...'} entry per line.")
    p.add_argument("--run_name", required=True,
                   help="Name for this run (used as output subdir + checkpoint key).")
    p.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Prompts per batch.")
    p.add_argument("--num_workers", type=int, default=DEFAULT_NUM_WORKERS,
                   help="Parallel worker processes.")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="Anthropic model to drive the agent loop.")
    p.add_argument("--max_turns", type=int, default=DEFAULT_MAX_TURNS,
                   help="Max tool-call iterations per prompt.")
    p.add_argument("--max_samples", type=int, default=None,
                   help="Process only the first N prompts (default: all).")
    p.add_argument("--system_prompt", default=None,
                   help="Optional system prompt to prepend to each conversation.")
    p.add_argument("--resume", action="store_true",
                   help="Resume from checkpoint (skip already-completed prompts).")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose logging (full tracebacks on failure).")
    p.add_argument("--output_root", type=Path, default=DEFAULT_OUTPUT_ROOT,
                   help="Root directory for per-run output.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        from tools.runtime import display_jarvis_home
        print("❌ ANTHROPIC_API_KEY is unset — set it in env or "
              f"{display_jarvis_home()}/keys.env before running.", file=sys.stderr)
        return 2

    try:
        runner = BatchRunner(
            dataset_file=args.dataset_file,
            run_name=args.run_name,
            batch_size=args.batch_size,
            num_workers=args.num_workers,
            model=args.model,
            max_turns=args.max_turns,
            max_samples=args.max_samples,
            system_prompt=args.system_prompt,
            verbose=args.verbose,
            output_root=args.output_root,
        )
    except FileNotFoundError as e:
        print(f"❌ dataset file not found: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"❌ failed to initialize: {e}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1

    try:
        runner.run(resume=args.resume)
    except KeyboardInterrupt:
        print("\n⚠️  interrupted; resume with --resume", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ fatal: {e}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

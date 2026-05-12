#!/usr/bin/env python3
"""One-shot CLI to migrate ~/.jarvis/learned_rules.md from v1 → v2.

Usage:
  bin/jarvis-rules-migrate-v2.py            # writes alongside as .v2.md
  bin/jarvis-rules-migrate-v2.py --in-place # overwrites learned_rules.md

Safe by default — writes to a sibling file unless --in-place is passed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "voice-agent"))

from pipeline.evolution.migrate import migrate_v1_to_v2  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--learned",
        type=Path,
        default=Path.home() / ".jarvis" / "learned_rules.md",
    )
    ap.add_argument(
        "--anchor",
        type=Path,
        default=REPO_ROOT / "src" / "voice-agent" / "prompts" / "anchor_rules.md",
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--in-place", action="store_true")
    args = ap.parse_args()

    if not args.learned.exists():
        print(f"v1 source file does not exist: {args.learned}", file=sys.stderr)
        return 1
    if args.in_place:
        backup = args.learned.with_suffix(".v1.bak.md")
        if not backup.exists():
            shutil.copy(args.learned, backup)
            print(f"Backed up v1 to {backup}")
        out = args.learned
    else:
        out = args.out or args.learned.with_suffix(".v2.md")

    migrate_v1_to_v2(v1_path=args.learned, anchor_path=args.anchor, out_path=out)
    print(f"Wrote v2 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

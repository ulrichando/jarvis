#!/usr/bin/env python3
"""Build the JARVIS skill index — a JSON catalog of the local skill library.

JARVIS-native adaptation of the upstream skills-index builder. Where the
upstream crawled remote skill marketplaces, this indexes the LOCAL library —
every ``SKILL.md`` discovered by ``pipeline.skills_loader`` — into a JSON
manifest (name, description, when_to_use, path). Useful for tooling / the
desktop UI and as a CI validation pass (every skill must parse with valid
frontmatter, no duplicate names).

Usage:
  scripts/build_skills_index.py                 # write <jarvis-home>/skills_index.json
  scripts/build_skills_index.py --out PATH      # custom output path
  scripts/build_skills_index.py --stdout        # print JSON to stdout (no write)
  scripts/build_skills_index.py --check         # validate only; non-zero exit on problems

Runs under the voice-agent's pinned .venv automatically (it re-execs itself
there if invoked with another interpreter) so ``pipeline.skills_loader``'s
dependencies resolve regardless of how it's launched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VA_DIR = _REPO_ROOT / "src" / "voice-agent"
_VENV_PY = _VA_DIR / ".venv" / "bin" / "python"

# Re-exec under the voice-agent venv so skills_loader's deps (PyYAML, …) are
# available even when this script is launched with the system Python.
if _VENV_PY.exists() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

sys.path.insert(0, str(_VA_DIR))


def build_index() -> list[dict]:
    """Return a sorted list of skill rows from the local library."""
    from pipeline.skills_loader import discover_skills  # noqa: E402 — after path setup

    skills = discover_skills()
    rows: list[dict] = []
    for name in sorted(skills):
        s = skills[name]
        rows.append(
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": (s.when_to_use or "").strip(),
                "path": str(s.path),
            }
        )
    return rows


def _default_out() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip() or str(Path.home() / ".jarvis")
    return Path(home) / "skills_index.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the JARVIS local skill index.")
    ap.add_argument("--out", type=Path, default=_default_out(), help="output JSON path")
    ap.add_argument("--stdout", action="store_true", help="print JSON to stdout (no write)")
    ap.add_argument("--check", action="store_true", help="validate only; non-zero exit on problems")
    args = ap.parse_args()

    rows = build_index()
    payload = {"count": len(rows), "skills": rows}

    if args.check:
        if not rows:
            print("FAIL: no skills discovered", file=sys.stderr)
            return 1
        names = [r["name"] for r in rows]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            print(f"FAIL: duplicate skill names: {dupes}", file=sys.stderr)
            return 1
        print(f"OK: {len(rows)} skills parsed cleanly, no duplicate names")
        return 0

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.stdout:
        print(text)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {len(rows)} skills → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

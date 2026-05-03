"""Drift detector for the duplicated TypeScript hub core.

Two copies of the runtime-agnostic client core exist by necessity —
Next.js Turbopack refuses to import code outside `src/web/`, so we
can't reach into `src/hub/` from a Next.js route. The fix is to keep
two byte-identical files and verify them in CI.

If this fails: run `bash scripts/check-hub-core-sync.sh --fix` to
propagate the canonical copy from src/hub/ into src/web/.
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HUB = REPO_ROOT / "src" / "hub" / "client-core.ts"
WEB = REPO_ROOT / "src" / "web" / "src" / "lib" / "hub" / "client-core.ts"


def test_hub_core_files_exist():
    assert HUB.is_file(), f"missing {HUB}"
    assert WEB.is_file(), f"missing {WEB}"


def test_hub_core_byte_identical():
    """src/hub/client-core.ts must be byte-identical to
    src/web/src/lib/hub/client-core.ts. If you intended to change the
    SDK core, edit the canonical copy at src/hub/client-core.ts and
    run `bash scripts/check-hub-core-sync.sh --fix`."""
    hub_bytes = HUB.read_bytes()
    web_bytes = WEB.read_bytes()
    assert hub_bytes == web_bytes, (
        f"\n{HUB} and\n{WEB}\n"
        f"have drifted ({len(hub_bytes)} vs {len(web_bytes)} bytes).\n"
        f"Fix: bash scripts/check-hub-core-sync.sh --fix"
    )

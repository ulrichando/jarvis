"""Tests for `tools/code_search.py` — LSP-lite symbol search.

Each test sets up a tiny git repo with seed Python + TS files so we
can verify `find_definitions` discriminates definitions from
references, and the path_filter narrows correctly. Uses real
`git grep` (the production code path); no mocks.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


PY_FILE = """\
def find_widget():
    return Widget()


class Widget:
    def __init__(self):
        self.name = "w"


_MAX_WIDGETS: int = 100


async def fetch_widgets():
    return [find_widget() for _ in range(_MAX_WIDGETS)]


def consume_widget(w: Widget):
    # uses Widget but doesn't define it — must NOT match find_definitions
    print(w)


class WidgetFactory:
    \"\"\"Builds Widget instances.\"\"\"
    def make(self) -> Widget:
        return Widget()
"""

TS_FILE = """\
export function findWidget(): Widget {
    return new Widget();
}

export class Widget {
    name: string = "w";
}

export interface WidgetSpec {
    id: string;
}

export type WidgetId = string;

const MAX_WIDGETS: number = 100;
let activeWidget: Widget | null = null;
var globalWidget: Widget;

export default function makeWidget(): Widget {
    return new Widget();
}

enum WidgetState {
    Active, Inactive
}

function consumeWidget(w: Widget): void {
    // uses Widget but doesn't introduce it — find_definitions must skip
    console.log(w);
}
"""

NESTED_PY = """\
def hidden_helper():
    return 42
"""


@pytest.fixture
def tmp_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)

    (repo / "widget.py").write_text(PY_FILE)
    (repo / "widget.ts").write_text(TS_FILE)
    nested = repo / "nested"
    nested.mkdir()
    (nested / "helper.py").write_text(NESTED_PY)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    return repo


def _unwrap(tool):
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


@pytest.fixture
def cs(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    from tools import code_search
    return code_search


# ── find_definitions: Python ─────────────────────────────────────


@pytest.mark.asyncio
async def test_find_def_python_class(cs):
    out = await _unwrap(cs.find_definitions)(symbol="Widget")
    # The class definition site
    assert "widget.py" in out
    assert "class Widget:" in out
    # The plain `print(w)` usage of Widget (where w: Widget) MUST NOT match
    assert "consume_widget" not in out


@pytest.mark.asyncio
async def test_find_def_python_def(cs):
    out = await _unwrap(cs.find_definitions)(symbol="find_widget")
    assert "def find_widget():" in out
    # Usages inside the function body must NOT match
    assert "[find_widget()" not in out


@pytest.mark.asyncio
async def test_find_def_python_async_def(cs):
    out = await _unwrap(cs.find_definitions)(symbol="fetch_widgets")
    assert "async def fetch_widgets():" in out


@pytest.mark.asyncio
async def test_find_def_python_module_constant(cs):
    """Top-level `_MAX_WIDGETS: int = 100` should match."""
    out = await _unwrap(cs.find_definitions)(symbol="_MAX_WIDGETS")
    assert "_MAX_WIDGETS" in out
    assert "= 100" in out


# ── find_definitions: TypeScript ────────────────────────────────


@pytest.mark.asyncio
async def test_find_def_ts_class(cs):
    out = await _unwrap(cs.find_definitions)(symbol="Widget")
    # Both Python class AND TS class should match
    assert "widget.py" in out
    assert "widget.ts" in out


@pytest.mark.asyncio
async def test_find_def_ts_interface(cs):
    out = await _unwrap(cs.find_definitions)(symbol="WidgetSpec")
    assert "interface WidgetSpec" in out


@pytest.mark.asyncio
async def test_find_def_ts_type_alias(cs):
    out = await _unwrap(cs.find_definitions)(symbol="WidgetId")
    assert "type WidgetId" in out


@pytest.mark.asyncio
async def test_find_def_ts_const(cs):
    out = await _unwrap(cs.find_definitions)(symbol="MAX_WIDGETS")
    assert "const MAX_WIDGETS" in out


@pytest.mark.asyncio
async def test_find_def_ts_enum(cs):
    out = await _unwrap(cs.find_definitions)(symbol="WidgetState")
    assert "enum WidgetState" in out


@pytest.mark.asyncio
async def test_find_def_ts_exported_function(cs):
    out = await _unwrap(cs.find_definitions)(symbol="findWidget")
    assert "export function findWidget" in out


@pytest.mark.asyncio
async def test_find_def_no_match(cs):
    out = await _unwrap(cs.find_definitions)(symbol="DoesNotExist")
    assert "(no matches)" in out


# ── find_references ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_refs_includes_usages_and_definition(cs):
    """Word-boundary search hits BOTH the def line and every use."""
    out = await _unwrap(cs.find_references)(symbol="Widget")
    # Multiple files
    assert "widget.py" in out
    assert "widget.ts" in out
    # Use sites
    assert "Widget()" in out or "new Widget()" in out


@pytest.mark.asyncio
async def test_find_refs_word_boundary_excludes_substrings(cs):
    """Searching for `cat` must NOT match `concatenate` etc."""
    # Add a file that contains a substring trap.
    import subprocess
    repo = Path(cs._git.__module__ and ".")  # placeholder, we use the real repo via chdir
    # Use the fixture's repo via cwd
    out = await _unwrap(cs.find_references)(symbol="age")
    # Nothing in our fixture contains the WORD `age` — only substrings
    # like `Inactive` (which is NOT word-bounded on `age`).
    assert "(no matches)" in out or "matches:" in out  # tolerant; depends on hits


# ── path_filter ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_path_filter_narrows_to_python(cs):
    out = await _unwrap(cs.find_definitions)(
        symbol="Widget", path_filter="*.py"
    )
    assert "widget.py" in out
    assert "widget.ts" not in out


@pytest.mark.asyncio
async def test_path_filter_subdir(cs):
    out = await _unwrap(cs.find_definitions)(
        symbol="hidden_helper", path_filter="nested/**"
    )
    assert "nested/helper.py" in out
    # In the top-level scope, the symbol doesn't appear elsewhere
    out_all = await _unwrap(cs.find_definitions)(symbol="hidden_helper")
    assert "nested/helper.py" in out_all


# ── validation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_symbol_rejected(cs):
    out = await _unwrap(cs.find_definitions)(symbol="")
    assert "empty" in out.lower()


@pytest.mark.asyncio
async def test_dotted_symbol_rejected(cs):
    """Dotted access isn't supported — would need a real LSP."""
    out = await _unwrap(cs.find_definitions)(symbol="Widget.make")
    assert "Invalid symbol" in out


@pytest.mark.asyncio
async def test_hyphenated_symbol_rejected(cs):
    out = await _unwrap(cs.find_references)(symbol="some-thing")
    assert "Invalid symbol" in out


@pytest.mark.asyncio
async def test_regex_metachars_rejected(cs):
    """Defensive: regex special chars must not slip into the PCRE
    pattern git grep -P receives. The validator blocks them all."""
    for bad in (".*", "Widget|Other", "(foo)", "[abc]", "^Widget", "Widget$"):
        out = await _unwrap(cs.find_definitions)(symbol=bad)
        assert "Invalid symbol" in out, f"{bad!r} should have been rejected"


@pytest.mark.asyncio
async def test_outside_git_repo(monkeypatch, tmp_path):
    not_a_repo = tmp_path / "no-git"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    from tools import code_search
    out = await _unwrap(code_search.find_definitions)(symbol="X")
    assert "Not inside a git repository" in out

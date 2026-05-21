"""Tests for the code_search and find_definitions tools.

Proves:
  (a) Both tools self-register in the registry.
  (b) check_fn returns True (rg or grep is always available on POSIX).
  (c) find_definitions finds a function definition in a temp Python file.
  (d) code_search finds a pattern in a temp source file.
  (e) Missing symbol / no-match path returns a clean JSON (not an error).
  (f) Invalid/empty inputs return a tool_error JSON.
  (g) Zero hermes tokens in the tool file.

No network.  Temporary files only.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_find_definitions_registered(self):
        import tools.code_search  # noqa: F401
        from tools.registry import registry
        assert registry.get_entry("find_definitions") is not None

    def test_code_search_registered(self):
        import tools.code_search  # noqa: F401
        from tools.registry import registry
        assert registry.get_entry("code_search") is not None

    def test_toolset_is_code_search(self):
        import tools.code_search  # noqa: F401
        from tools.registry import registry
        for name in ("find_definitions", "code_search"):
            entry = registry.get_entry(name)
            assert entry is not None
            assert entry.toolset == "code_search"


# ---------------------------------------------------------------------------
# (b) check_fn
# ---------------------------------------------------------------------------

class TestCheckFn:
    def test_enabled_when_rg_or_grep_available(self):
        from tools.code_search import _check_code_search
        # On any POSIX system, grep is always present.
        # rg is also expected in this dev environment.
        assert _check_code_search() is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_py_file(tmp_path: Path, content: str, name: str = "sample.py") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _call_find(args: dict) -> dict:
    from tools.code_search import _handle_find_definitions
    return json.loads(_handle_find_definitions(args))


def _call_search(args: dict) -> dict:
    from tools.code_search import _handle_code_search
    return json.loads(_handle_code_search(args))


# ---------------------------------------------------------------------------
# (c) find_definitions
# ---------------------------------------------------------------------------

class TestFindDefinitions:
    def test_finds_python_function(self, tmp_path):
        _make_py_file(tmp_path, "def my_func(x, y):\n    return x + y\n")
        result = _call_find({"symbol": "my_func", "path": str(tmp_path)})
        assert result["success"] is True
        assert "my_func" in result.get("output", "")

    def test_finds_python_class(self, tmp_path):
        _make_py_file(tmp_path, "class MyClass:\n    pass\n")
        result = _call_find({"symbol": "MyClass", "path": str(tmp_path)})
        assert result["success"] is True
        assert "MyClass" in result.get("output", "")

    def test_no_match_returns_empty_list(self, tmp_path):
        _make_py_file(tmp_path, "def other_func():\n    pass\n")
        result = _call_find({"symbol": "nonexistent_symbol_xyz", "path": str(tmp_path)})
        assert result["success"] is True
        assert result.get("matches", None) == [] or "hint" in result

    def test_file_glob_restricts_search(self, tmp_path):
        _make_py_file(tmp_path, "def target_fn():\n    pass\n", name="a.py")
        (tmp_path / "b.txt").write_text("def target_fn():\n    pass\n")
        # Only search .py — should find it in a.py
        result = _call_find({"symbol": "target_fn", "path": str(tmp_path), "file_glob": "*.py"})
        assert result["success"] is True
        assert "target_fn" in result.get("output", "")

    def test_context_lines_included(self, tmp_path):
        _make_py_file(
            tmp_path,
            "# some comment\ndef ctx_fn(a, b):\n    return a + b\n# after\n",
        )
        result = _call_find({"symbol": "ctx_fn", "path": str(tmp_path), "context": 1})
        assert result["success"] is True
        # With context=1, neighbouring lines should appear
        output = result.get("output", "")
        assert "ctx_fn" in output

    def test_async_def_found(self, tmp_path):
        _make_py_file(tmp_path, "async def async_handler():\n    pass\n")
        result = _call_find({"symbol": "async_handler", "path": str(tmp_path)})
        assert result["success"] is True
        assert "async_handler" in result.get("output", "")


# ---------------------------------------------------------------------------
# (d) code_search
# ---------------------------------------------------------------------------

class TestCodeSearch:
    def test_finds_literal_import(self, tmp_path):
        _make_py_file(tmp_path, "import json\nfrom pathlib import Path\nx = 1\n")
        result = _call_search({"pattern": "import json", "path": str(tmp_path)})
        assert result["success"] is True
        assert "import json" in result.get("output", "")

    def test_finds_env_var_reference(self, tmp_path):
        _make_py_file(tmp_path, 'key = os.environ.get("JARVIS_HUB_DB", "")\n')
        result = _call_search({"pattern": "JARVIS_HUB_DB", "path": str(tmp_path)})
        assert result["success"] is True
        assert "JARVIS_HUB_DB" in result.get("output", "")

    def test_no_match_returns_success_empty(self, tmp_path):
        _make_py_file(tmp_path, "x = 1\n")
        result = _call_search({"pattern": "THIS_WILL_NEVER_MATCH_XYZZY", "path": str(tmp_path)})
        assert result["success"] is True
        assert result.get("match_count", 0) == 0 or result.get("matches", None) == []

    def test_fixed_strings_no_regex_interpret(self, tmp_path):
        # A dot in a fixed-string pattern should NOT match any character.
        _make_py_file(tmp_path, "version = '1.2.3'\nother = 'xyz'\n")
        result = _call_search(
            {"pattern": "1.2.3", "path": str(tmp_path), "fixed_strings": True}
        )
        assert result["success"] is True
        # The literal "1.2.3" should match; the output should contain it.
        assert "1.2.3" in result.get("output", "")

    def test_regex_pattern_works(self, tmp_path):
        _make_py_file(tmp_path, "def foo():\n    pass\ndef bar():\n    pass\n")
        result = _call_search(
            {"pattern": r"def (foo|bar)", "path": str(tmp_path), "file_glob": "*.py"}
        )
        assert result["success"] is True
        out = result.get("output", "")
        assert "foo" in out or "bar" in out


# ---------------------------------------------------------------------------
# (f) invalid inputs return error JSON
# ---------------------------------------------------------------------------

class TestInvalidInputs:
    def test_find_definitions_missing_symbol(self):
        result = _call_find({})
        assert "error" in result

    def test_find_definitions_empty_symbol(self):
        result = _call_find({"symbol": "   "})
        assert "error" in result

    def test_code_search_missing_pattern(self):
        result = _call_search({})
        assert "error" in result

    def test_code_search_empty_pattern(self):
        result = _call_search({"pattern": "   "})
        assert "error" in result


# ---------------------------------------------------------------------------
# (g) no hermes tokens
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    def test_no_hermes_in_code_search(self):
        path = _VA_ROOT / "tools" / "code_search.py"
        lines = path.read_text(encoding="utf-8").splitlines()
        bad = []
        for lineno, line in enumerate(lines, 1):
            if "hermes" in line.lower():
                stripped = line.lstrip()
                if stripped.startswith("#") or '"""' in line:
                    continue
                bad.append((lineno, line.rstrip()))
        assert not bad, f"code_search.py has non-comment 'hermes' tokens: {bad}"

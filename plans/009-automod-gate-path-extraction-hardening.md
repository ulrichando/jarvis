# Plan 009: Close the three diff-path-extraction bypasses in the auto-mod safety gate

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat e04d31c8..HEAD -- src/voice-agent/pipeline/automod/test_gate.py src/voice-agent/pipeline/automod/_state.py src/voice-agent/tests/test_automod_test_gate.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `e04d31c8`, 2026-06-27

## Why this matters

`test_gate.validate_diff()` is the central safety gate of JARVIS's self-modifying
auto-mod loop. It is the code shared by **two of the three** "independent"
enforcement layers (`finalize.py:244` at build time and `cli.py:115` at merge
time) — a single bug here defeats both at once. It currently has **three proven
ways to slip a file past the blocklist**, all empirically confirmed and none
covered by a test:

1. **Rename into a blocked path** — the header regex captures only the `a/`
   (source) side, so a rename `a/src/voice-agent/foo.py b/src/voice-agent/sanitizers/x.py`
   is judged on `foo.py` (allowed) while the content lands in the blocked
   `sanitizers/` dir.
2. **Quoted path** — git quotes paths containing spaces/special chars
   (`"a/src/voice-agent/sanitizers/ev il.py"`); the regex `^diff --git a/(\S+)`
   doesn't match the leading quote, so that file is **invisible** to the gate and
   rides along in a mixed diff.
3. **`..` traversal** — `is_blocked_path` does `lstrip("./")` (a character-set
   strip) with no path normalization, so `src/voice-agent/../sanitizers/dsml.py`
   returns `is_blocked_path == False`.

The loop is OFF by default today, so harm is currently bounded — but these are
exactly the holes that must be closed before `JARVIS_AUTOMOD_SPAWN_LIVE=1` is
ever flipped. After this plan the gate fails **closed** on any header it cannot
cleanly parse and checks **both** sides of every diff header after normalizing
the path.

## Current state

- `src/voice-agent/pipeline/automod/test_gate.py` — the gate. Today:
  ```python
  _FILE_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)

  def files_changed(diff_text: str) -> list[str]:
      seen: list[str] = []
      seen_set: set[str] = set()
      for m in _FILE_HEADER_RE.finditer(diff_text or ""):
          path = m.group(1)            # <-- ONLY the a/ side
          if path not in seen_set:
              seen_set.add(path)
              seen.append(path)
      return seen

  def validate_diff(diff_text: str) -> tuple[bool, str]:
      ...
      paths = files_changed(diff_text)
      if not paths:
          return False, "no_diff_headers"
      max_files = _max_files()
      if len(paths) > max_files:
          return False, f"too_many_files:{len(paths)}>{max_files}"
      for path in paths:
          if is_blocked_path(path):
              return False, f"blocked_path:{path}"
      ... (test-deletion / skip-marker / line-count checks unchanged) ...
  ```
- `src/voice-agent/pipeline/automod/_state.py:139` — the predicate. Today:
  ```python
  def is_blocked_path(path: str) -> bool:
      p = path.strip().lstrip("./")    # <-- char-set strip, no normalization
      for blocked in HARD_BLOCKLIST_PATHS:
          if p == blocked or p.startswith(blocked):
              return True
      if not p.startswith(ALLOWED_PATH_PREFIX):
          return True
      return False
  ```
  `HARD_BLOCKLIST_PATHS` entries are repo-relative, dir entries carry a trailing
  slash (e.g. `"src/voice-agent/sanitizers/"`), file entries do not (e.g.
  `"src/voice-agent/confab_detector.py"`). `ALLOWED_PATH_PREFIX = "src/voice-agent/"`.
- `src/voice-agent/tests/test_automod_test_gate.py` — 16 tests, **every** test
  diff uses identical `a/X b/X` paths, so renames/quoting/`..` are never
  exercised. Use this file's existing style (plain `def test_*`, build a diff
  string, call `validate_diff`, assert) as the pattern for the new tests.

Repo conventions: stdlib-only in `_state.py` (it documents "import-safe, stdlib
only, no import-time side effects" — do **not** add third-party imports).
`test_gate.py` already imports `os`, `re`, and `from pipeline.automod._state import is_blocked_path`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Drift check | `git diff --stat e04d31c8..HEAD -- src/voice-agent/pipeline/automod/test_gate.py src/voice-agent/pipeline/automod/_state.py` | empty (no drift) |
| Gate tests | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_test_gate.py -q` | all pass |
| Throttle tests (uses `is_blocked_path`) | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_throttle.py tests/test_automod_finalize.py tests/test_automod_cli.py -q` | all pass |
| Full automod safety subset | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_test_gate.py tests/test_automod_throttle.py tests/test_automod_finalize.py tests/test_automod_cli.py tests/test_automod_spawner.py tests/test_automod_plan.py -q` | all pass |

## Scope

**In scope** (the only files you should modify):
- `src/voice-agent/pipeline/automod/test_gate.py`
- `src/voice-agent/pipeline/automod/_state.py`
- `src/voice-agent/tests/test_automod_test_gate.py` (add tests)

**Out of scope** (do NOT touch):
- `HARD_BLOCKLIST_PATHS` / `ALLOWED_PATH_PREFIX` **values** — do not add or remove
  entries; only change the *matching logic* of `is_blocked_path`. Adding/removing
  blocklist entries needs separate user sign-off (`.claude/rules/regression-prevention.md` §8).
- `finalize.py` / `cli.py` — they call `validate_diff` and need no change; the fix
  is entirely inside the gate.
- Any other `pipeline/automod/*.py`.

## Git workflow

- Branch off `master`: `git checkout -b advisor/009-gate-path-hardening`.
- **CRITICAL — this repo carries 100+ uncommitted files from parallel agent
  sessions.** NEVER `git add -A` or `git commit -a`. Stage only your in-scope
  files explicitly and commit with an explicit pathspec:
  `git add src/voice-agent/pipeline/automod/test_gate.py src/voice-agent/pipeline/automod/_state.py src/voice-agent/tests/test_automod_test_gate.py`
  then `git commit -- <those same paths>`. Verify with `git show --stat HEAD`
  that ONLY those three files are in the commit.
- Conventional-commit message, e.g. `fix(automod): close diff-path-extraction blocklist bypasses (rename/quoted/..)`.
- **No `Co-Authored-By` trailer and no "Generated with Claude Code" attribution** (CLAUDE.md rule).
- Do NOT push or open a PR unless the operator instructs it.

## Steps

### Step 1: Normalize the path in `is_blocked_path` (`_state.py`)

Replace the body of `is_blocked_path` so it strips quotes, normalizes `.`/`..`
segments with `posixpath.normpath` (git paths are always forward-slash), and
rejects anything absolute or escaping the repo root. Add `import posixpath` at
the top of `_state.py` next to the existing `import os`.

Target shape:
```python
import posixpath  # add near the existing `import os`

def is_blocked_path(path: str) -> bool:
    """True if `path` (repo-relative) is in the hard blocklist OR is
    outside the allowed prefix. Normalizes the path first so `..` traversal,
    quoting, and `./` prefixes cannot slip past. Used by throttle, finalize,
    and merge."""
    p = path.strip().strip('"').strip()
    if not p:
        return True  # empty/garbage path → fail closed
    p = posixpath.normpath(p)
    # Absolute, or escapes the repo root via leading `..` → never allowed.
    if p.startswith("/") or p == ".." or p.startswith("../"):
        return True
    for blocked in HARD_BLOCKLIST_PATHS:
        if p == blocked.rstrip("/") or p.startswith(blocked):
            return True
    if not p.startswith(ALLOWED_PATH_PREFIX):
        return True
    return False
```
Note `blocked.rstrip("/")` handles the exact-directory case after `normpath`
strips a trailing slash; the `startswith(blocked)` (blocked keeps its trailing
slash) handles files *inside* a blocked dir.

**Verify**:
```
cd src/voice-agent && .venv/bin/python -c "
from pipeline.automod._state import is_blocked_path as b
assert b('src/voice-agent/x.py') is False
assert b('src/voice-agent/sanitizers/planted.py') is True
assert b('src/voice-agent/../sanitizers/dsml.py') is True
assert b('src/voice-agent/sanitizers/ev il.py') is True
assert b('\"src/voice-agent/sanitizers/x\"') is True
assert b('bin/jarvis-automod') is True
print('is_blocked_path OK')
"
```
→ prints `is_blocked_path OK`.

### Step 2: Parse both sides of every header + fail closed (`test_gate.py`)

Replace the header regex and `files_changed`, and add a both-sides blocklist
sweep with a fail-closed guard in `validate_diff`. `files_changed` should return
the **destination** (`b/`) path per header (that's where content lands) so the
file-count and the artifact's `files_changed` field stay meaningful; a new helper
returns *every* path (both sides) for the blocklist check.

Target shape (replace the regex + `files_changed`, and the count/blocklist block
inside `validate_diff`):
```python
# Matches both unquoted (`a/path`) and quoted (`"a/path with space"`) specs.
_FILE_HEADER_RE = re.compile(
    r'^diff --git (?P<a>"[^"]*"|\S+) (?P<b>"[^"]*"|\S+)', re.MULTILINE
)


def _strip_spec(spec: str) -> str:
    """Turn a header spec (`a/path`, `b/path`, or a quoted form) into a bare
    repo-relative path."""
    s = spec.strip().strip('"')
    if s.startswith("a/") or s.startswith("b/"):
        s = s[2:]
    return s


def _header_paths(diff_text: str) -> list[tuple[str, str]]:
    """(old, new) path pair per `diff --git` header."""
    return [
        (_strip_spec(m.group("a")), _strip_spec(m.group("b")))
        for m in _FILE_HEADER_RE.finditer(diff_text or "")
    ]


def files_changed(diff_text: str) -> list[str]:
    """Destination (b/) path per header, deduped, in order of first appearance.
    Destination is where content lands (and equals the source for a plain
    edit/new file)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for _old, new in _header_paths(diff_text):
        if new and new not in seen_set:
            seen_set.add(new)
            seen.append(new)
    return seen
```
Then inside `validate_diff`, after the `if not diff_text...` empty check,
replace the `paths = files_changed(...)` / count / blocklist block with:
```python
    # Fail closed: every `diff --git` header must parse into a path pair. A
    # header we cannot parse (exotic quoting) must REJECT, never be skipped.
    n_headers = len(re.findall(r"^diff --git ", diff_text, re.MULTILINE))
    pairs = _header_paths(diff_text)
    if n_headers == 0:
        return False, "no_diff_headers"
    if len(pairs) != n_headers:
        return False, "unparseable_diff_header"

    paths = files_changed(diff_text)        # destination paths (count + display)
    max_files = _max_files()
    if len(paths) > max_files:
        return False, f"too_many_files:{len(paths)}>{max_files}"

    # Blocklist BOTH sides of every header (a rename's source AND destination).
    for old, new in pairs:
        for candidate in (old, new):
            if candidate and is_blocked_path(candidate):
                return False, f"blocked_path:{candidate}"
```
Leave the test-deletion, skip-marker, and line-count checks below it unchanged.

**Verify**:
```
cd src/voice-agent && .venv/bin/python -c "
from pipeline.automod.test_gate import validate_diff
ren='diff --git a/src/voice-agent/foo.py b/src/voice-agent/sanitizers/x.py\nrename from src/voice-agent/foo.py\nrename to src/voice-agent/sanitizers/x.py\n'
assert validate_diff(ren)[0] is False, 'rename bypass still open'
q='diff --git a/src/voice-agent/clean.py b/src/voice-agent/clean.py\n--- a/x\n+++ b/x\n@@\n+x\n'+'diff --git \"a/src/voice-agent/sanitizers/ev il.py\" \"b/src/voice-agent/sanitizers/ev il.py\"\n--- a/x\n+++ b/x\n@@\n+p\n'
assert validate_diff(q)[0] is False, 'quoted bypass still open'
ok='diff --git a/src/voice-agent/x.py b/src/voice-agent/x.py\n--- a/src/voice-agent/x.py\n+++ b/src/voice-agent/x.py\n@@ -1 +1 @@\n-a\n+b\n'
assert validate_diff(ok)[0] is True, ok
print('validate_diff bypasses closed')
"
```
→ prints `validate_diff bypasses closed`.

### Step 3: Add characterization tests

Append these tests to `src/voice-agent/tests/test_automod_test_gate.py`, matching
the file's existing style:
- `test_rejects_rename_into_blocked_path` — a rename header `a/src/voice-agent/foo.py b/src/voice-agent/sanitizers/x.py`; assert `not ok` and `"block" in reason.lower()`.
- `test_rejects_quoted_blocked_path_in_mixed_diff` — one clean file header + one quoted `"a/src/voice-agent/sanitizers/ev il.py" "b/..."` header; assert `not ok`.
- `test_rejects_dotdot_traversal_path` — header `a/src/voice-agent/../sanitizers/dsml.py b/src/voice-agent/../sanitizers/dsml.py`; assert `not ok`.
- `test_unparseable_header_fails_closed` — a diff whose `diff --git` line has only one spec (e.g. `"diff --git a/onlyone\n--- a/x\n+++ b/x\n@@\n+y\n"`); assert `not ok` and `reason == "unparseable_diff_header"`.
- `test_files_changed_uses_destination_for_rename` — `files_changed` on the rename header returns `["src/voice-agent/sanitizers/x.py"]` (the destination).
- Also add a `_state` unit test `test_is_blocked_path_normalizes_traversal` asserting `is_blocked_path("src/voice-agent/../sanitizers/dsml.py") is True` and `is_blocked_path("src/voice-agent/ok.py") is False`.

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_test_gate.py -q` → all pass, including the 6 new tests. The pre-existing 16 tests must still pass (the clean-diff/`a/X b/X` cases are unaffected because old==new there).

## Test plan

- New tests live in `tests/test_automod_test_gate.py` (model after the existing
  `test_rejects_blocked_path_sanitizers` for diff-string construction).
- Cases: rename→blocked (the #1 bypass), quoted-in-mixed (the #2 bypass),
  `..` traversal (the #3 bypass), unparseable-header fail-closed, rename
  destination accounting, and the `_state` normalization unit.
- Verification: the gate-subset command (all pass) **and** the full automod safety
  subset command in "Commands you will need" (no regression in callers of
  `is_blocked_path` / `files_changed`).

## Done criteria

ALL must hold:

- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_test_gate.py tests/test_automod_throttle.py tests/test_automod_finalize.py tests/test_automod_cli.py tests/test_automod_spawner.py tests/test_automod_plan.py -q` exits 0
- [ ] The Step 2 and Step 1 inline `-c` verification snippets print their success lines
- [ ] `git show --stat HEAD` lists ONLY the three in-scope files
- [ ] `grep -n 'lstrip("./")' src/voice-agent/pipeline/automod/_state.py` returns nothing (old logic gone)
- [ ] `plans/README.md` status row for 009 updated

## STOP conditions

Stop and report (do not improvise) if:
- The "Current state" excerpts don't match the live code (drift since `e04d31c8`).
- Any pre-existing test in `test_automod_test_gate.py` goes red after the change
  and the fix isn't an obvious adjustment to the new logic.
- Closing the gate requires editing `finalize.py`, `cli.py`, or the blocklist
  *values* — that's out of scope; report instead.
- The full automod safety subset reveals a caller that depended on `files_changed`
  returning the `a/` side.

## Maintenance notes

- **These files are on the auto-mod `HARD_BLOCKLIST`.** This plan must be executed
  by a human or a normal dev executor — **never** by the auto-mod loop itself (it
  is structurally forbidden from editing `pipeline/automod/` and `_state.py`).
- The fail-closed `unparseable_diff_header` path is deliberate: if git ever emits
  a header shape this parser can't read, the gate rejects rather than waves it
  through. If a *legitimate* proposal ever trips it, fix the parser — do not relax
  it to "skip unknown headers".
- A reviewer should scrutinize that `files_changed` still returns one entry per
  changed file for plain edits (old==new), so the daily-cap/`too_many_files`
  semantics are unchanged for the common case.
- Deferred: full git C-quoting (octal-escaped unicode inside quotes) is not
  decoded — such a path is partially extracted and still blocklist-checked, and a
  gross mismatch trips the fail-closed guard. Decode it properly only if a real
  proposal ever needs a unicode-named file under `src/voice-agent/`.

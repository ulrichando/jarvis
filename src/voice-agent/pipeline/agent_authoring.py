"""Authoring + discovery core for user subagents — validation, rendering,
and guarded atomic writes to ``~/.jarvis/agents/<name>.md``.

Parallels :mod:`pipeline.skills_authoring` but for Claude-Code-style *agent*
definitions: the markdown files ``bin/jarvis`` discovers and dispatches as
``--agent <name>``. Writing a file here is what makes
``dispatch_agent(subagent_type="<name>")`` able to spawn a user-authored
agent — ``bin/jarvis`` scans ``~/.jarvis/agents/`` (its userSettings root)
regardless of cwd, so a file we write is immediately dispatchable.

Format mirrors the CLI's ``formatAgentAsMarkdown``
(src/cli/src/components/agents/agentFileUtils.ts) so a file written here
parses cleanly under the CLI's own loader::

    ---
    name: <agentType>
    description: "<when-to-use, YAML double-quoted, \\n-escaped>"
    tools: Tool1, Tool2          # omitted entirely => agent inherits all tools
    model: <model>               # optional
    ---

    <system prompt body>

Validation mirrors the CLI's ``validateAgentType`` (name charset + length)
and the body/description minimums in ``validateAgent``, so what we author is
accepted by the CLI.

Shipped/project agents (under the repo's ``.jarvis/agents/``) and the
built-in dispatch agents are READ-ONLY here — same shape as shipped skills:
copy to a new name to customize. Only files under the user root are mutated.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.agent_authoring")

# Mirror the CLI's validateAgent.ts limits.
MAX_NAME_LENGTH = 50
MIN_NAME_LENGTH = 3
MIN_BODY_CHARS = 20
MAX_DESCRIPTION_CHARS = 5000
MAX_AGENT_CONTENT_CHARS = 100_000

# CLI: ^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$ — start/end alphanumeric, hyphens
# allowed in the middle. Mixed case is permitted (the built-ins Explore/Plan
# are capitalized); dispatch matches the name case-sensitively.
VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$")

# Names reserved by dispatch_agent's built-in roster. A user file with one of
# these names would never be dispatched (built-ins are matched first), so we
# refuse to create a dead file under a reserved name. Kept here (not imported
# from tools.dispatch_agent) to avoid a pipeline→tools import cycle; these four
# are stable and documented in dispatch_agent._POLICY.
RESERVED_DISPATCH_NAMES = frozenset({"explore", "researcher", "code_reviewer", "plan"})

# Flat single-line frontmatter — agent files use simple `key: value` pairs
# (the `description` value is YAML double-quoted with escaped newlines). We
# only need name/description/tools/model for discovery + field-preserving
# edits, so a tiny reader beats pulling in the skills block-scalar parser.
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


# ── frontmatter ────────────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(frontmatter_dict, body)``. Empty dict + full text when no
    frontmatter block opens the file. Only top-level (unindented) ``key:``
    lines are captured; surrounding quotes are stripped from values."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in raw.splitlines():
        if line[:1] in (" ", "\t"):  # indented continuation — not a flat key
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        fm[key] = val
    return fm, body


def _escape_desc(s: str) -> str:
    """Escape a when-to-use string for a YAML double-quoted scalar — exactly
    the CLI's order: backslash, then quote, then newline → literal ``\\n``."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\\\n")
    )


def _display_desc(s: str) -> str:
    """Lossy un-escape of an on-disk description for voice/list display."""
    s = s.replace("\\n", " ").replace('\\"', '"').replace("\\\\", "\\")
    return " ".join(s.split())


def render_agent_md(
    name: str,
    description: str,
    body: str,
    tools: Optional[object] = None,
    model: Optional[str] = None,
) -> str:
    """Compose an agent ``SKILL``-style markdown file. ``tools`` may be a
    comma-separated string or a list; ``None`` / ``["*"]`` omits the line
    (agent inherits all tools, matching the CLI)."""
    name = (name or "").strip()
    description = (description or "").strip()
    body = (body or "").strip()

    lines = ["---", f"name: {name}", f'description: "{_escape_desc(description)}"']

    toks: list[str] = []
    if isinstance(tools, str):
        toks = [t.strip() for t in tools.split(",") if t.strip()]
    elif tools is not None:
        toks = [str(t).strip() for t in tools if str(t).strip()]
    if toks and not (len(toks) == 1 and toks[0] == "*"):
        lines.append("tools: " + ", ".join(toks))

    if model and str(model).strip():
        lines.append(f"model: {str(model).strip()}")

    lines.append("---")
    return "\n".join(lines) + "\n\n" + body + "\n"


# ── validation ─────────────────────────────────────────────────────────


def validate_name(name: str) -> Optional[str]:
    """Return an error string if ``name`` is not a valid agent name, else None."""
    if not name:
        return "Agent name is required."
    if len(name) < MIN_NAME_LENGTH:
        return f"Agent name must be at least {MIN_NAME_LENGTH} characters."
    if len(name) > MAX_NAME_LENGTH:
        return f"Agent name must be {MAX_NAME_LENGTH} characters or fewer."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid agent name {name!r}. Use letters, digits, and hyphens; "
            f"it must start and end with a letter or digit (no spaces, dots, "
            f"or slashes)."
        )
    return None


def _validate_create_fields(
    description: str, body: str
) -> Optional[str]:
    if not description:
        return "Agent 'description' (when to use it) is required."
    if len(description) > MAX_DESCRIPTION_CHARS:
        return f"Agent description exceeds {MAX_DESCRIPTION_CHARS} characters."
    if not body:
        return "Agent system-prompt body is required."
    if len(body) < MIN_BODY_CHARS:
        return f"Agent system-prompt body is too short (min {MIN_BODY_CHARS} chars)."
    return None


# ── roots / discovery ──────────────────────────────────────────────────


def _agents_roots() -> list[Path]:
    """Discovery roots, lowest→highest precedence (user wins on name
    collision). Overridable via ``JARVIS_AGENTS_PATHS`` (colon-separated,
    PATH-shaped) for test isolation — same convention as JARVIS_SKILLS_PATHS.

    Defaults mirror what ``bin/jarvis`` scans: the repo's project agents
    (``<repo>/.jarvis/agents``) and the user root (``~/.jarvis/agents``).
    The user root is the writable target — cwd-independent, so a file we
    write there is always discoverable by ``bin/jarvis --agent``."""
    env = os.environ.get("JARVIS_AGENTS_PATHS")
    if env:
        return [Path(p) for p in env.split(":") if p]
    repo_root = Path(__file__).resolve().parents[3]
    return [
        repo_root / ".jarvis" / "agents",     # project agents (read-only here)
        Path.home() / ".jarvis" / "agents",   # user-managed (writable target)
    ]


def _user_agents_root() -> Path:
    """The writable user agents root — the LAST discovery root."""
    return _agents_roots()[-1]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def discover_agents() -> list[dict]:
    """Scan all roots for ``<name>.md`` agent files. Returns a name-sorted
    list of ``{name, description, path, editable}`` (user root entries shadow
    same-named project entries; ``editable`` is True only for the user root)."""
    user_root = _user_agents_root()
    seen: dict[str, dict] = {}
    for root in _agents_roots():
        if not root.is_dir():
            continue
        editable = _is_under(root, user_root) and root.resolve() == user_root.resolve()
        for p in sorted(root.glob("*.md")):
            try:
                fm, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
            except Exception as e:  # pragma: no cover — skip an unreadable file
                log.warning(f"[agents] cannot read {p}: {e}")
                continue
            name = (fm.get("name") or p.stem).strip()
            if not name:
                continue
            seen[name] = {
                "name": name,
                "description": _display_desc(fm.get("description", "")),
                "path": str(p),
                "editable": editable,
            }
    return sorted(seen.values(), key=lambda d: d["name"])


def find_agent(name: str) -> Optional[dict]:
    """Return the discovery record for ``name`` (exact match), or None."""
    name = (name or "").strip()
    if not name:
        return None
    for a in discover_agents():
        if a["name"] == name:
            return a
    return None


# ── writes ─────────────────────────────────────────────────────────────


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + os.replace), creating
    parents and cleaning up the temp file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_user_agent(name: str) -> tuple[Optional[dict], Optional[str]]:
    """Return ``(record, None)`` if ``name`` is an editable user agent, else
    ``(None, error_string)``."""
    name = (name or "").strip()
    a = find_agent(name)
    if a is None:
        editable = ", ".join(
            x["name"] for x in discover_agents() if x["editable"]
        ) or "(none)"
        return None, f"No agent named {name!r}. Editable user agents: {editable}"
    if not a["editable"]:
        return None, (
            f"Agent {name!r} is a built-in or project agent (read-only). "
            f"Copy it to a new name to customize it."
        )
    return a, None


def create_user_agent(
    name: str,
    description: str,
    body: str,
    tools: Optional[object] = None,
    model: Optional[str] = None,
) -> dict:
    """Create a new user agent at ``~/.jarvis/agents/<name>.md``.
    Returns ``{ok, error?, path?, shadow?}``. ``shadow`` is True when a
    same-named built-in/project agent already exists (the new user file does
    not override dispatch's built-ins, which are matched first)."""
    name = (name or "").strip()
    err = validate_name(name)
    if err:
        return {"ok": False, "error": err}
    if name in RESERVED_DISPATCH_NAMES:
        return {
            "ok": False,
            "error": f"{name!r} is a built-in dispatch agent name — dispatch would "
                     f"always pick the built-in, so a file here would be dead. "
                     f"Choose a different name.",
        }
    description = (description or "").strip()
    body = (body or "").strip()
    ferr = _validate_create_fields(description, body)
    if ferr:
        return {"ok": False, "error": ferr}

    existing = find_agent(name)
    if existing and existing["editable"]:
        return {
            "ok": False,
            "error": f"A user agent named {name!r} already exists; use action "
                     f"'edit' to change it (or 'delete' first).",
        }
    shadow = bool(existing and not existing["editable"])

    content = render_agent_md(name, description, body, tools, model)
    if len(content) > MAX_AGENT_CONTENT_CHARS:
        return {"ok": False, "error": "Agent file exceeds the size limit."}

    target = _user_agents_root() / f"{name}.md"
    from tools import file_safety  # reuse the direct-write denylist
    denial = file_safety.write_denial_message(str(target))
    if denial:
        return {"ok": False, "error": denial}

    try:
        _atomic_write_text(target, content)
    except OSError as e:
        return {"ok": False, "error": f"could not write agent: {type(e).__name__}: {e}"}

    log.info(f"[agents] created user agent {name!r} at {target}")
    return {"ok": True, "path": str(target), "shadow": shadow}


def edit_user_agent(
    name: str,
    body: str,
    description: Optional[str] = None,
    tools: Optional[object] = None,
    model: Optional[str] = None,
) -> dict:
    """Rewrite an existing user agent's system-prompt ``body``.

    Field-preservation contract (avoids YAML re-escape round-trip bugs):
      * ``description is None`` AND no ``tools``/``model`` given → the
        frontmatter block is preserved VERBATIM; only the body changes.
      * otherwise → the frontmatter is rebuilt: ``description`` from the
        argument (required in this branch), ``tools``/``model`` from the
        argument or, if omitted, the values parsed from the existing file.
    """
    a, err = _resolve_user_agent(name)
    if err:
        return {"ok": False, "error": err}
    body = (body or "").strip()
    if len(body) < MIN_BODY_CHARS:
        return {"ok": False, "error": f"Agent body is too short (min {MIN_BODY_CHARS} chars)."}

    path = Path(a["path"])
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)

    if description is None and tools is None and model is None and m:
        # Preserve the frontmatter block exactly; swap only the body.
        content = f"---\n{m.group(1)}\n---\n\n{body}\n"
    else:
        fm, _ = _parse_frontmatter(raw)
        if description is None:
            return {
                "ok": False,
                "error": "Changing an agent's tools/model also requires passing "
                         "'description' (the when-to-use), so it can be re-rendered safely.",
            }
        desc = description.strip()
        if not desc:
            return {"ok": False, "error": "Agent 'description' cannot be empty."}
        if len(desc) > MAX_DESCRIPTION_CHARS:
            return {"ok": False, "error": f"Agent description exceeds {MAX_DESCRIPTION_CHARS} characters."}
        tls = tools if tools is not None else fm.get("tools")
        mdl = model if model is not None else fm.get("model")
        content = render_agent_md(name.strip(), desc, body, tls, mdl)

    from tools import file_safety
    denial = file_safety.write_denial_message(str(path))
    if denial:
        return {"ok": False, "error": denial}

    try:
        _atomic_write_text(path, content)
    except OSError as e:
        return {"ok": False, "error": f"could not write agent: {type(e).__name__}: {e}"}

    log.info(f"[agents] edited user agent {name!r}")
    return {"ok": True, "path": str(path)}


def patch_user_agent(
    name: str, old_string: str, new_string: str, replace_all: bool = False
) -> dict:
    """Targeted old→new replacement anywhere in a user agent's markdown
    (frontmatter or body). Re-validates that the result still parses with a
    non-empty name + a long-enough body before writing."""
    a, err = _resolve_user_agent(name)
    if err:
        return {"ok": False, "error": err}
    path = Path(a["path"])
    content = path.read_text(encoding="utf-8")
    count = content.count(old_string) if old_string else 0
    if count == 0:
        return {"ok": False, "error": f"old_string not found in agent {name!r}."}
    if count > 1 and not replace_all:
        return {
            "ok": False,
            "error": (
                f"old_string appears {count}× in {name!r}; pass replace_all=true "
                f"or give a longer, unique string."
            ),
        }
    new_content = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    fm, body = _parse_frontmatter(new_content)
    if not fm.get("name") or len((body or "").strip()) < MIN_BODY_CHARS:
        return {"ok": False, "error": "patch would produce an invalid agent file."}

    from tools import file_safety
    denial = file_safety.write_denial_message(str(path))
    if denial:
        return {"ok": False, "error": denial}
    try:
        _atomic_write_text(path, new_content)
    except OSError as e:
        return {"ok": False, "error": f"could not write agent: {type(e).__name__}: {e}"}

    log.info(f"[agents] patched user agent {name!r}")
    return {"ok": True, "path": str(path)}


def _trash_root() -> Path:
    """Recoverable-delete destination — sibling of the user agents root and
    OUTSIDE the discovery tree (so trashed agents aren't re-discovered).
    ``~/.jarvis/.agents-trash/`` in production."""
    return _user_agents_root().parent / ".agents-trash"


def delete_user_agent(name: str) -> dict:
    """Recoverably delete a user agent by moving its file to the trash root.
    Returns ``{ok, error?, trashed_to?}``."""
    a, err = _resolve_user_agent(name)
    if err:
        return {"ok": False, "error": err}

    src = Path(a["path"])
    user_root = _user_agents_root()
    if not _is_under(src, user_root):  # pragma: no cover — _resolve guards this
        return {"ok": False, "error": f"refusing to delete {name!r}: outside the user agents root."}

    trash_root = _trash_root()
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = trash_root / f"{name}-{stamp}.md"
    try:
        shutil.move(str(src), str(dest))
    except OSError as e:
        return {"ok": False, "error": f"could not delete agent: {type(e).__name__}: {e}"}

    log.info(f"[agents] deleted user agent {name!r} → {dest}")
    return {"ok": True, "trashed_to": str(dest)}

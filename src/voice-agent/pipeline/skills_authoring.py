"""Authoring core for user skills — validation, rendering, and guarded
atomic writes to ~/.jarvis/skills/. Keeps pipeline/skills_loader.py
pure-read; this module owns all skill mutation.

Write target is ALWAYS the user skills root (the last discovery root —
~/.jarvis/skills/ in production, or the JARVIS_SKILLS_PATHS tail in
tests). Shipped skills under src/voice-agent/skills/ are read-only source
and are never modified or deleted by these functions.

Validation ports hermes/tools/skill_manager_tool.py limits but uses the
loader's hand-rolled frontmatter parser (no PyYAML dependency).
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

from pipeline.skills_loader import (
    SKILLS,
    Skill,
    _default_roots,
    _parse_frontmatter,
    reload_skills,
)

log = logging.getLogger("jarvis.skills_authoring")

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000

# Hyphens only (stricter than Hermes' [a-z0-9._-]) — matches the existing
# JARVIS skill naming (git-status, system-stats) and forecloses any
# dot/underscore path-traversal ambiguity. Single segment, no slashes.
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_name(name: str) -> Optional[str]:
    """Return an error string if `name` is not a valid skill name, else None."""
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name {name!r}. Use lowercase letters, digits, and "
            f"hyphens; must start with a letter or digit (no slashes/dots)."
        )
    return None


def validate_skill_markdown(content: str) -> Optional[str]:
    """Return an error string if `content` is not a valid SKILL.md, else None.

    Mirrors hermes _validate_frontmatter, using the loader's parser.
    """
    if not content or not content.strip():
        return "Content cannot be empty."
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return f"SKILL.md exceeds {MAX_SKILL_CONTENT_CHARS} characters."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (a '---' line at byte 0)."
    fm, body = _parse_frontmatter(content)
    if not fm:
        return "SKILL.md frontmatter is missing or not closed with a '---' line."
    name = str(fm.get("name", "")).strip()
    if not name:
        return "Frontmatter must include a non-empty 'name' field."
    name_err = validate_name(name)
    if name_err:
        return name_err
    description = str(fm.get("description", "")).strip()
    if not description:
        return "Frontmatter must include a non-empty 'description' field."
    if len(description) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    if not body.strip():
        return "SKILL.md must have a non-empty body after the frontmatter."
    return None


def render_skill_md(
    name: str, description: str, when_to_use: str, body: str
) -> str:
    """Compose a SKILL.md string from fields. Block-scalar for multi-line
    when_to_use (2-space indent — matches the loader's block parser)."""
    name = (name or "").strip()
    description = (description or "").strip()
    when_to_use = (when_to_use or "").strip()
    body = (body or "").strip()

    lines = ["---", f"name: {name}", f"description: {description}"]
    if when_to_use:
        if "\n" in when_to_use:
            lines.append("when_to_use: |")
            lines.extend("  " + ln for ln in when_to_use.splitlines())
        else:
            lines.append(f"when_to_use: {when_to_use}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body + "\n"


def _user_skills_root() -> Path:
    """The writable user skills root — the LAST discovery root (user wins
    over shipped). ~/.jarvis/skills/ in production; the JARVIS_SKILLS_PATHS
    tail in tests."""
    return _default_roots()[-1]


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (temp file + os.replace). Creates
    parent dirs. Cleans up the temp file on failure."""
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


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def create_user_skill(
    name: str, description: str, when_to_use: str, body: str
) -> dict:
    """Create a new user skill. Returns {ok, error?, path?, shadow?}."""
    name = (name or "").strip()
    err = validate_name(name)
    if err:
        return {"ok": False, "error": err}
    content = render_skill_md(name, description, when_to_use, body)
    verr = validate_skill_markdown(content)
    if verr:
        return {"ok": False, "error": verr}

    # Shadow detection BEFORE the write: an existing same-name skill whose
    # file lives outside the user root is a shipped skill we'd override.
    existing = SKILLS.get(name)
    shadow = existing is not None and not _is_under(
        existing.path, _user_skills_root()
    )

    target = _user_skills_root() / name / "SKILL.md"

    # Reuse the same write-denylist the direct write tool uses.
    from tools import file_safety
    denial = file_safety.write_denial_message(str(target))
    if denial:
        return {"ok": False, "error": denial}

    try:
        _atomic_write_text(target, content)
    except OSError as e:
        return {"ok": False, "error": f"could not write skill: {type(e).__name__}: {e}"}

    reload_skills()
    log.info(f"[skills] created user skill {name!r} at {target}")
    return {"ok": True, "path": str(target), "shadow": shadow}


def _user_skill_names() -> list[str]:
    """Names of skills whose file lives under the user root (editable)."""
    root = _user_skills_root()
    return sorted(s.name for s in SKILLS.all() if _is_under(s.path, root))


def _resolve_user_skill(name: str) -> tuple[Optional[Skill], Optional[str]]:
    """Return (skill, None) if `name` is an editable user skill, else
    (None, error_string)."""
    name = (name or "").strip()
    sk = SKILLS.get(name)
    if sk is None:
        avail = ", ".join(_user_skill_names()) or "(none)"
        return None, f"No skill named {name!r}. Editable user skills: {avail}"
    if not _is_under(sk.path, _user_skills_root()):
        return None, (
            f"Skill {name!r} is a shipped (read-only) skill. Shipped skills "
            f"can't be modified or deleted; copy it to a new name instead."
        )
    return sk, None


def _write_validated(path: Path, content: str) -> dict:
    """Validate `content`, run the write-denylist, write atomically, reload."""
    verr = validate_skill_markdown(content)
    if verr:
        return {"ok": False, "error": f"result would be invalid: {verr}"}
    from tools import file_safety
    denial = file_safety.write_denial_message(str(path))
    if denial:
        return {"ok": False, "error": denial}
    try:
        _atomic_write_text(path, content)
    except OSError as e:
        return {"ok": False, "error": f"could not write skill: {type(e).__name__}: {e}"}
    reload_skills()
    return {"ok": True, "path": str(path)}


def patch_user_skill(
    name: str, old_string: str, new_string: str, replace_all: bool = False
) -> dict:
    """Targeted old→new replacement in an existing user skill."""
    sk, err = _resolve_user_skill(name)
    if err:
        return {"ok": False, "error": err}
    content = sk.path.read_text(encoding="utf-8")
    count = content.count(old_string) if old_string else 0
    if count == 0:
        return {"ok": False, "error": f"old_string not found in skill {name!r}."}
    if count > 1 and not replace_all:
        return {
            "ok": False,
            "error": (
                f"old_string appears {count}× in {name!r}; pass replace_all=true "
                f"or give a longer, unique string."
            ),
        }
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)
    res = _write_validated(sk.path, new_content)
    if res["ok"]:
        log.info(f"[skills] patched user skill {name!r}")
    return res


def edit_user_skill(
    name: str,
    body: str,
    description: Optional[str] = None,
    when_to_use: Optional[str] = None,
) -> dict:
    """Full body rewrite of an existing user skill. Frontmatter preserved
    unless description/when_to_use are supplied."""
    sk, err = _resolve_user_skill(name)
    if err:
        return {"ok": False, "error": err}
    desc = sk.description if description is None else description
    wtu = sk.when_to_use if when_to_use is None else when_to_use
    content = render_skill_md(name.strip(), desc, wtu, body)
    res = _write_validated(sk.path, content)
    if res["ok"]:
        log.info(f"[skills] rewrote user skill {name!r}")
    return res


def _trash_root() -> Path:
    """Recoverable-delete destination, sibling of the user skills root and
    OUTSIDE the discovery tree (so trashed skills aren't re-discovered).
    ~/.jarvis/.skills-trash/ in production."""
    return _user_skills_root().parent / ".skills-trash"


def delete_user_skill(name: str) -> dict:
    """Recoverably delete a user skill by moving it to the trash root.
    Returns {ok, error?, trashed_to?}."""
    sk, err = _resolve_user_skill(name)
    if err:
        return {"ok": False, "error": err}

    user_root = _user_skills_root()
    skill_dir = sk.path.parent
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    trash_root = _trash_root()
    trash_root.mkdir(parents=True, exist_ok=True)

    try:
        if skill_dir.resolve() == user_root.resolve():
            # Flat <name>.md skill living directly in the root: move just the
            # file, never the whole root.
            dest = trash_root / f"{name}-{stamp}.md"
            shutil.move(str(sk.path), str(dest))
        else:
            # Directory-style <name>/SKILL.md: confine then move the subdir.
            if not _is_under(skill_dir, user_root):
                return {
                    "ok": False,
                    "error": f"refusing to delete {name!r}: outside the user skills root.",
                }
            dest = trash_root / f"{name}-{stamp}"
            shutil.move(str(skill_dir), str(dest))
    except OSError as e:
        return {"ok": False, "error": f"could not delete skill: {type(e).__name__}: {e}"}

    reload_skills()
    log.info(f"[skills] deleted user skill {name!r} → {dest}")
    return {"ok": True, "trashed_to": str(dest)}

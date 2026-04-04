"""
Loads markdown files from .jarvis/output-styles (and .claude/output-styles for
compatibility) directories throughout the project and from ~/.jarvis/output-styles
directory and converts them to output styles.

Each filename becomes a style name, and the file content becomes the style prompt.
The frontmatter provides name and description.

Structure:
- Project .jarvis/output-styles/*.md -> project styles
- User ~/.jarvis/output-styles/*.md -> user styles (overridden by project styles)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass
class OutputStyleConfig:
    name: str
    description: str
    prompt: str
    source: str
    keep_coding_instructions: Optional[bool] = None


def _coerce_description_to_string(
    desc: object, style_name: str
) -> Optional[str]:
    """Coerce a frontmatter description value to a string or None."""
    if desc is None:
        return None
    if isinstance(desc, str):
        return desc
    return str(desc)


def _extract_description_from_markdown(
    content: str, default: str
) -> str:
    """Extract the first paragraph from markdown content as a description."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return default


def _load_markdown_files_for_subdir(
    subdir: str, cwd: str
) -> list[dict]:
    """
    Load markdown files from .jarvis/<subdir> directories (also .claude/ for compat).

    Searches project directories and user home directory.
    Returns list of dicts with filePath, frontmatter, content, source.
    """
    import yaml

    results: list[dict] = []
    search_dirs: list[tuple[str, str]] = []

    # Project directories (.jarvis preferred, .claude for compatibility)
    for folder in (".jarvis", ".claude"):
        project_dir = os.path.join(cwd, folder, subdir)
        if os.path.isdir(project_dir):
            search_dirs.append((project_dir, "project"))
            break  # prefer .jarvis

    # User home directory
    home = os.path.expanduser("~")
    jarvis_home = os.environ.get("JARVIS_HOME", os.path.join(home, ".jarvis"))
    user_dir = os.path.join(jarvis_home, subdir)
    if os.path.isdir(user_dir):
        search_dirs.append((user_dir, "user"))

    for dir_path, source in search_dirs:
        try:
            for fname in os.listdir(dir_path):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(dir_path, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        raw = f.read()

                    frontmatter: dict = {}
                    content = raw

                    if raw.startswith("---"):
                        parts = raw.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                frontmatter = yaml.safe_load(parts[1]) or {}
                            except Exception:
                                frontmatter = {}
                            content = parts[2]

                    results.append(
                        {
                            "filePath": fpath,
                            "frontmatter": frontmatter,
                            "content": content,
                            "source": source,
                        }
                    )
                except Exception:
                    continue
        except OSError:
            continue

    return results


_output_style_cache: Optional[list[OutputStyleConfig]] = None


async def get_output_style_dir_styles(cwd: str) -> list[OutputStyleConfig]:
    """
    Load output styles from .jarvis/output-styles directories.

    Memoized: call clear_output_style_caches() to reset.
    """
    global _output_style_cache
    if _output_style_cache is not None:
        return _output_style_cache

    try:
        markdown_files = _load_markdown_files_for_subdir("output-styles", cwd)

        styles: list[OutputStyleConfig] = []
        for mf in markdown_files:
            try:
                file_path: str = mf["filePath"]
                frontmatter: dict = mf["frontmatter"]
                content: str = mf["content"]
                source: str = mf["source"]

                file_name = os.path.basename(file_path)
                style_name = file_name.removesuffix(".md")

                name = frontmatter.get("name", style_name)
                if not isinstance(name, str):
                    name = str(name)

                description = _coerce_description_to_string(
                    frontmatter.get("description"), style_name
                )
                if description is None:
                    description = _extract_description_from_markdown(
                        content,
                        f"Custom {style_name} output style",
                    )

                # Parse keep-coding-instructions flag
                keep_raw = frontmatter.get("keep-coding-instructions")
                keep_coding_instructions: Optional[bool] = None
                if keep_raw is True or keep_raw == "true":
                    keep_coding_instructions = True
                elif keep_raw is False or keep_raw == "false":
                    keep_coding_instructions = False

                # Warn if force-for-plugin is set on non-plugin output style
                if "force-for-plugin" in frontmatter:
                    import logging

                    logging.warning(
                        'Output style "%s" has force-for-plugin set, '
                        "but this option only applies to plugin output styles. "
                        "Ignoring.",
                        name,
                    )

                styles.append(
                    OutputStyleConfig(
                        name=name,
                        description=description,
                        prompt=content.strip(),
                        source=source,
                        keep_coding_instructions=keep_coding_instructions,
                    )
                )
            except Exception:
                import logging
                import traceback

                logging.error(traceback.format_exc())

        _output_style_cache = styles
        return styles
    except Exception:
        import logging
        import traceback

        logging.error(traceback.format_exc())
        return []


def clear_output_style_caches() -> None:
    """Clear all output style caches."""
    global _output_style_cache
    _output_style_cache = None

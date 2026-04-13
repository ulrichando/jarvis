"""JARVIS Prompt Builder — context-rich system prompt construction.

Ported from claw-code's prompt.rs. Discovers project instructions,
git status, and environment context to build rich system prompts.

Discovery order for instruction files:
1. .jarvis/JARVIS.md (project-level)
2. JARVIS.md (project root)
3. .jarvis/instructions.md
4. Walk up parent directories for JARVIS.md

Each file is capped at MAX_INSTRUCTION_CHARS to prevent prompt injection.
Total instruction content is capped at MAX_TOTAL_INSTRUCTION_CHARS.
"""

import os
import subprocess
import platform
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("jarvis.prompt")

MAX_INSTRUCTION_CHARS = 20000
MAX_TOTAL_INSTRUCTION_CHARS = 50000


@dataclass
class InstructionFile:
    """A discovered project instruction file."""
    path: Path
    content: str
    source: str = ""  # "project", "parent", "user"


@dataclass
class ProjectContext:
    """Discovered context about the current project."""
    cwd: Path = field(default_factory=Path.cwd)
    git_status: str = ""
    git_branch: str = ""
    git_diff_summary: str = ""
    instruction_files: list[InstructionFile] = field(default_factory=list)
    detected_stack: list[str] = field(default_factory=list)
    codebase_index: str = ""  # Compact project index (two-tier: tree + cached symbols)


class PromptBuilder:
    """Builds rich system prompts with project context."""

    def __init__(self, cwd: str = None):
        self.cwd = Path(cwd) if cwd else Path.cwd()

    def discover_context(self, include_git: bool = False) -> ProjectContext:
        """Discover project context: instructions (and optionally git info, stack).

        Git commands are skipped by default — brain.py injects git context only when
        the user explicitly asks about git. Passing include_git=True enables them for
        callers that need the full context (e.g. PromptBuilder.build()).
        """
        ctx = ProjectContext(cwd=self.cwd)

        # Discover instruction files (cheap — file system only)
        ctx.instruction_files = self._discover_instructions()

        if include_git:
            # Git info — only when the caller needs it
            ctx.git_branch = self._git("rev-parse --abbrev-ref HEAD")
            ctx.git_status = self._git("status --short --branch")
            diff_stat = self._git("diff --stat")
            if diff_stat:
                ctx.git_diff_summary = diff_stat
            ctx.detected_stack = self._detect_stack()

        return ctx

    def build(self, base_prompt: str, context: ProjectContext = None) -> str:
        """Build the full system prompt with context."""
        if context is None:
            context = self.discover_context()

        sections = [base_prompt]

        # Date
        utc_now = datetime.now(timezone.utc)
        sections.append(f"\n═══ ENVIRONMENT ═══")
        sections.append(f"Date: {utc_now.strftime('%Y-%m-%d')}")
        sections.append(f"CWD: {context.cwd}")
        sections.append(f"OS: {platform.system()} {platform.release()}")
        sections.append(f"Host: {platform.node()}")

        if context.detected_stack:
            sections.append(f"Stack: {', '.join(context.detected_stack)}")

        # Git context
        if context.git_branch:
            sections.append(f"\n═══ GIT ═══")
            sections.append(f"Branch: {context.git_branch}")
            if context.git_status:
                # Only show first 20 lines of status
                lines = context.git_status.strip().split("\n")
                if len(lines) > 20:
                    status = "\n".join(lines[:20]) + f"\n... ({len(lines) - 20} more files)"
                else:
                    status = context.git_status.strip()
                sections.append(f"Status:\n{status}")
            if context.git_diff_summary:
                sections.append(f"Changes:\n{context.git_diff_summary.strip()}")

        # Project instructions
        if context.instruction_files:
            total_chars = 0
            sections.append(f"\n═══ PROJECT INSTRUCTIONS ═══")

            for inst in context.instruction_files:
                if total_chars >= MAX_TOTAL_INSTRUCTION_CHARS:
                    sections.append(f"(remaining instruction files omitted — {MAX_TOTAL_INSTRUCTION_CHARS} char limit)")
                    break

                content = inst.content
                if len(content) > MAX_INSTRUCTION_CHARS:
                    content = content[:MAX_INSTRUCTION_CHARS] + f"\n... (truncated at {MAX_INSTRUCTION_CHARS} chars)"

                remaining = MAX_TOTAL_INSTRUCTION_CHARS - total_chars
                if len(content) > remaining:
                    content = content[:remaining] + "\n... (total instruction limit reached)"

                total_chars += len(content)
                sections.append(f"\n# From {inst.path.name} ({inst.source})")
                sections.append(content)

        return "\n".join(sections)

    def _discover_instructions(self) -> list[InstructionFile]:
        """Discover instruction files by walking up from cwd."""
        files = []
        seen_paths = set()

        # Project-level files (highest priority)
        candidates = [
            (self.cwd / ".jarvis" / "JARVIS.md", "project"),
            (self.cwd / "JARVIS.md", "project"),
            (self.cwd / ".jarvis" / "instructions.md", "project"),
            (self.cwd / "CLAUDE.md", "project"),  # Also support CLAUDE.md
            (self.cwd / ".claude" / "instructions.md", "project"),
        ]

        for path, source in candidates:
            if path.exists() and path.resolve() not in seen_paths:
                try:
                    content = path.read_text(errors="replace")
                    if content.strip():
                        files.append(InstructionFile(path=path, content=content, source=source))
                        seen_paths.add(path.resolve())
                except Exception as e:
                    log.warning("Failed to read instruction file %s: %s", path, e)

        # Walk up parent directories
        current = self.cwd.parent
        depth = 0
        while current != current.parent and depth < 5:
            for name in ("JARVIS.md", "CLAUDE.md"):
                path = current / name
                if path.exists() and path.resolve() not in seen_paths:
                    try:
                        content = path.read_text(errors="replace")
                        if content.strip():
                            files.append(InstructionFile(path=path, content=content, source="parent"))
                            seen_paths.add(path.resolve())
                    except Exception:
                        pass
            current = current.parent
            depth += 1

        # User-level instructions
        jarvis_home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
        user_inst = jarvis_home / "instructions.md"
        if user_inst.exists() and user_inst.resolve() not in seen_paths:
            try:
                content = user_inst.read_text(errors="replace")
                if content.strip():
                    files.append(InstructionFile(path=user_inst, content=content, source="user"))
            except Exception:
                pass

        return files

    def _detect_stack(self) -> list[str]:
        """Detect the project's technology stack."""
        stack = []

        checks = {
            "Python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
            "Rust": ["Cargo.toml"],
            "JavaScript": ["package.json"],
            "TypeScript": ["tsconfig.json"],
            "Go": ["go.mod"],
            "Java": ["pom.xml", "build.gradle"],
            "C/C++": ["CMakeLists.txt", "Makefile", "meson.build"],
            "Docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
        }

        for tech, markers in checks.items():
            for marker in markers:
                if (self.cwd / marker).exists():
                    stack.append(tech)
                    break

        # Framework detection
        pkg_json = self.cwd / "package.json"
        if pkg_json.exists():
            try:
                import json
                pkg = json.loads(pkg_json.read_text())
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps:
                    stack.append("Next.js")
                elif "react" in deps:
                    stack.append("React")
                if "vue" in deps:
                    stack.append("Vue")
                if "@nestjs/core" in deps:
                    stack.append("NestJS")
            except Exception:
                pass

        return stack

    def _git(self, cmd: str) -> str:
        """Run a git command, return output or empty string."""
        try:
            result = subprocess.run(
                f"git --no-optional-locks {cmd}",
                shell=True, capture_output=True, text=True,
                timeout=5, cwd=str(self.cwd),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

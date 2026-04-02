"""CodeEngine — language-agnostic code analysis and LLM request helpers.

No LLM dependency — analysis is purely structural.  explain() and generate()
return *prompts* suitable for passing to a reasoner.
"""

import re
from pathlib import Path
from typing import Optional

# Extension → language mapping
_EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".lua": "lua",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".r": "r",
    ".R": "r",
    ".swift": "swift",
    ".cs": "csharp",
    ".zig": "zig",
    ".nim": "nim",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".dart": "dart",
}

# Language-specific function/def patterns
_FUNC_PATTERNS: dict[str, re.Pattern] = {
    "python":     re.compile(r"^\s*(?:async\s+)?def\s+\w+", re.MULTILINE),
    "javascript": re.compile(r"(?:^\s*(?:async\s+)?function\s+\w+|^\s*(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\()", re.MULTILINE),
    "typescript": re.compile(r"(?:^\s*(?:async\s+)?function\s+\w+|^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\()", re.MULTILINE),
    "rust":       re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+", re.MULTILINE),
    "go":         re.compile(r"^\s*func\s+", re.MULTILINE),
    "c":          re.compile(r"^\w[\w\s\*]+\s+\w+\s*\(", re.MULTILINE),
    "cpp":        re.compile(r"^\w[\w\s\*:&<>]+\s+\w+\s*\(", re.MULTILINE),
    "java":       re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+\w+\s*\(", re.MULTILINE),
    "ruby":       re.compile(r"^\s*def\s+\w+", re.MULTILINE),
    "shell":      re.compile(r"^\s*\w+\s*\(\)\s*\{", re.MULTILINE),
    "lua":        re.compile(r"^\s*(?:local\s+)?function\s+\w+", re.MULTILINE),
    "php":        re.compile(r"^\s*(?:public|private|protected)?\s*function\s+\w+", re.MULTILINE),
}

# Import patterns per language
_IMPORT_PATTERNS: dict[str, re.Pattern] = {
    "python":     re.compile(r"^\s*(?:import|from)\s+\S+", re.MULTILINE),
    "javascript": re.compile(r"^\s*(?:import\s|const\s+\w+\s*=\s*require)", re.MULTILINE),
    "typescript": re.compile(r"^\s*(?:import\s|const\s+\w+\s*=\s*require)", re.MULTILINE),
    "rust":       re.compile(r"^\s*use\s+\S+", re.MULTILINE),
    "go":         re.compile(r'^\s*(?:import\s+"[^"]+"|import\s+\()', re.MULTILINE),
    "c":          re.compile(r'^\s*#include\s+[<"]', re.MULTILINE),
    "cpp":        re.compile(r'^\s*#include\s+[<"]', re.MULTILINE),
    "java":       re.compile(r"^\s*import\s+\S+", re.MULTILINE),
    "ruby":       re.compile(r"^\s*require\s+", re.MULTILINE),
    "shell":      re.compile(r"^\s*(?:source|\.)\s+", re.MULTILINE),
}

# Class patterns
_CLASS_PATTERNS: dict[str, re.Pattern] = {
    "python":     re.compile(r"^\s*class\s+\w+", re.MULTILINE),
    "javascript": re.compile(r"^\s*class\s+\w+", re.MULTILINE),
    "typescript": re.compile(r"^\s*(?:export\s+)?class\s+\w+", re.MULTILINE),
    "rust":       re.compile(r"^\s*(?:pub\s+)?struct\s+\w+", re.MULTILINE),
    "go":         re.compile(r"^\s*type\s+\w+\s+struct", re.MULTILINE),
    "java":       re.compile(r"^\s*(?:public|private)?\s*class\s+\w+", re.MULTILINE),
    "ruby":       re.compile(r"^\s*class\s+\w+", re.MULTILINE),
    "php":        re.compile(r"^\s*class\s+\w+", re.MULTILINE),
}


class CodeEngine:
    """Structural code analysis — no LLM needed."""

    # ── Detection ─────────────────────────────────────────────────────

    @staticmethod
    def detect_language(path: str) -> str:
        """Detect programming language from file extension."""
        ext = Path(path).suffix.lower()
        return _EXT_MAP.get(ext, "unknown")

    # ── Analysis ──────────────────────────────────────────────────────

    def analyze(self, code: str, language: str = "python") -> dict:
        """Return structural metrics for *code*.

        Keys: lines, blank_lines, comment_lines, functions, classes,
              imports, language.
        """
        lines = code.splitlines()
        total = len(lines)
        blank = sum(1 for l in lines if not l.strip())

        # Comments (line-level heuristic)
        comment_chars = {"python": "#", "ruby": "#", "shell": "#",
                         "rust": "//", "go": "//", "javascript": "//",
                         "typescript": "//", "c": "//", "cpp": "//",
                         "java": "//", "php": "//", "lua": "--"}
        cc = comment_chars.get(language, "#")
        comments = sum(1 for l in lines if l.strip().startswith(cc))

        funcs = len(_FUNC_PATTERNS.get(language, _FUNC_PATTERNS["python"]).findall(code))
        classes = len(_CLASS_PATTERNS.get(language, re.compile(r"^\s*class\s+\w+", re.MULTILINE)).findall(code))
        imports = len(_IMPORT_PATTERNS.get(language, _IMPORT_PATTERNS.get("python", re.compile(r"^$"))).findall(code))

        return {
            "language": language,
            "lines": total,
            "blank_lines": blank,
            "comment_lines": comments,
            "code_lines": total - blank - comments,
            "functions": funcs,
            "classes": classes,
            "imports": imports,
        }

    # ── LLM request builders (no LLM call — just prompt construction) ─

    @staticmethod
    def explain(code: str, language: Optional[str] = None) -> str:
        """Return a prompt asking an LLM to explain *code*."""
        lang_hint = f" ({language})" if language else ""
        return (
            f"Explain the following{lang_hint} code clearly and concisely. "
            f"Cover what it does, key logic, and any notable patterns:\n\n"
            f"```{language or ''}\n{code}\n```"
        )

    @staticmethod
    def generate(description: str, language: str = "python") -> str:
        """Return a prompt asking an LLM to generate code."""
        return (
            f"Write {language} code that does the following:\n\n"
            f"{description}\n\n"
            f"Return ONLY the code, no explanations. "
            f"Include brief inline comments for clarity."
        )

    # ── Convenience ───────────────────────────────────────────────────

    def analyze_file(self, path: str) -> dict:
        """Read a file and return its analysis."""
        p = Path(path)
        if not p.is_file():
            return {"error": f"File not found: {path}"}
        language = self.detect_language(path)
        code = p.read_text(errors="replace")
        result = self.analyze(code, language)
        result["path"] = str(p.resolve())
        return result

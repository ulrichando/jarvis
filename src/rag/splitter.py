"""Text splitting — LangChain RecursiveCharacterTextSplitter with good defaults.

Code files use language-aware splitters; everything else uses the recursive
character splitter which respects paragraph/sentence boundaries.
"""

import logging
from typing import List

log = logging.getLogger(__name__)

# (chunk_size, chunk_overlap) by content type
_PROFILES = {
    "default": (1000, 150),
    "code":    (800,  100),
    "pdf":     (1200, 200),
    "web":     (800,  120),
    "chat":    (500,  80),
}

_CODE_EXTENSIONS = {
    ".py": "python", ".js": "js", ".ts": "ts", ".tsx": "ts",
    ".jsx": "js", ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".sh": "bash", ".md": "markdown",
}


def get_splitter(profile: str = "default", file_ext: str | None = None):
    """Return an appropriate LangChain text splitter for the given profile."""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
    except ImportError:
        raise RuntimeError(
            "langchain-text-splitters not installed. Run: pip install langchain-text-splitters"
        )

    # Language-aware splitter for code files
    if file_ext and file_ext.lower() in _CODE_EXTENSIONS:
        lang_str = _CODE_EXTENSIONS[file_ext.lower()]
        try:
            lang = Language(lang_str)
            chunk_size, overlap = _PROFILES["code"]
            return RecursiveCharacterTextSplitter.from_language(
                language=lang, chunk_size=chunk_size, chunk_overlap=overlap
            )
        except ValueError:
            pass  # Fallback to default if language not supported

    chunk_size, overlap = _PROFILES.get(profile, _PROFILES["default"])
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def split_text(text: str, profile: str = "default", file_ext: str | None = None) -> List[str]:
    """Split a text string into chunks."""
    splitter = get_splitter(profile=profile, file_ext=file_ext)
    return splitter.split_text(text)

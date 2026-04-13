"""Document loaders — wraps LangChain community loaders.

Supports:
  - Local files: .pdf, .txt, .md, .py, .js, .ts, .json, .yaml, .csv, .html
  - Web URLs: BeautifulSoup-based web page loader
  - Raw text/string: direct ingestion
  - Directories: recursive file discovery

Returns list of (text, metadata) tuples ready for chunking.
"""

import logging
import mimetypes
import os
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

Document = Tuple[str, dict]  # (page_content, metadata)


def _metadata(source: str, extra: dict | None = None) -> dict:
    m = {"source": source}
    if extra:
        m.update(extra)
    return m


def load_file(path: str) -> List[Document]:
    """Load a single file. Auto-detects type."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = p.suffix.lower()

    if ext == ".pdf":
        return _load_pdf(str(p))
    elif ext in (".txt", ".md", ".rst"):
        return _load_text(str(p))
    elif ext == ".csv":
        return _load_csv(str(p))
    elif ext in (".html", ".htm"):
        return _load_html_file(str(p))
    else:
        # Generic text fallback — works for .py, .js, .ts, .json, .yaml, etc.
        return _load_text(str(p))


def _load_pdf(path: str) -> List[Document]:
    try:
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(path)
        docs = loader.load()
        return [(d.page_content, {**d.metadata, "source": path}) for d in docs]
    except ImportError:
        # Fallback: pypdf directly
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            pages = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append((text, _metadata(path, {"page": i + 1})))
            return pages
        except ImportError:
            raise RuntimeError("Install pypdf: pip install pypdf")


def _load_text(path: str) -> List[Document]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return [(text, _metadata(path))]
    except Exception as e:
        log.warning("Failed to load %s: %s", path, e)
        return []


def _load_csv(path: str) -> List[Document]:
    try:
        from langchain_community.document_loaders import CSVLoader
        loader = CSVLoader(path)
        docs = loader.load()
        return [(d.page_content, {**d.metadata, "source": path}) for d in docs]
    except ImportError:
        # Fallback: read raw
        return _load_text(path)


def _load_html_file(path: str) -> List[Document]:
    try:
        from langchain_community.document_loaders import BSHTMLLoader
        loader = BSHTMLLoader(path)
        docs = loader.load()
        return [(d.page_content, {**d.metadata, "source": path}) for d in docs]
    except ImportError:
        return _load_text(path)


def load_url(url: str) -> List[Document]:
    """Load a web page via URL."""
    try:
        from langchain_community.document_loaders import WebBaseLoader
        loader = WebBaseLoader(url)
        docs = loader.load()
        return [(d.page_content, {**d.metadata, "source": url}) for d in docs]
    except ImportError:
        # Fallback: requests + bs4
        try:
            import requests
            from bs4 import BeautifulSoup
            resp = requests.get(url, timeout=15, headers={"User-Agent": "JARVIS/3.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            return [(text, _metadata(url))]
        except Exception as e:
            log.warning("URL load failed %s: %s", url, e)
            return []


def load_directory(path: str, glob: str = "**/*", suffixes: List[str] | None = None) -> List[Document]:
    """Recursively load all text files in a directory."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    allowed = set(suffixes or [
        ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
        ".go", ".rs", ".java", ".c", ".cpp", ".cs", ".rb", ".sh",
        ".json", ".yaml", ".yml", ".toml", ".html", ".csv", ".pdf",
    ])

    docs: List[Document] = []
    for fp in sorted(p.rglob("*")):
        if fp.is_file() and fp.suffix.lower() in allowed:
            try:
                docs.extend(load_file(str(fp)))
            except Exception as e:
                log.debug("Skipping %s: %s", fp, e)
    return docs


def load_string(text: str, source: str = "inline") -> List[Document]:
    """Wrap a raw string as a document."""
    return [(text, _metadata(source))]

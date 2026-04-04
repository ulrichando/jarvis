"""PDF reading, validation, and page extraction utilities."""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Literal, Optional, Union

# Limits
PDF_TARGET_RAW_SIZE = 20 * 1024 * 1024  # ~20MB
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # ~100MB


@dataclass
class PDFError:
    reason: Literal["empty", "too_large", "password_protected", "corrupted", "unknown", "unavailable"]
    message: str


@dataclass
class PDFFileData:
    file_path: str
    base64_data: str
    original_size: int


@dataclass
class PDFSuccess:
    type: str
    file: PDFFileData


@dataclass
class PDFExtractData:
    file_path: str
    original_size: int
    count: int
    output_dir: str


@dataclass
class PDFExtractSuccess:
    type: str
    file: PDFExtractData


PDFResult = Union[PDFSuccess, PDFError]
PDFExtractResult = Union[PDFExtractSuccess, PDFError]


def _format_file_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


async def read_pdf(file_path: str) -> Union[PDFSuccess, PDFError]:
    """Read a PDF file and return it as base64-encoded data."""
    try:
        stat = os.stat(file_path)
        original_size = stat.st_size

        if original_size == 0:
            return PDFError(reason="empty", message=f"PDF file is empty: {file_path}")

        if original_size > PDF_TARGET_RAW_SIZE:
            return PDFError(
                reason="too_large",
                message=f"PDF file exceeds maximum allowed size of {_format_file_size(PDF_TARGET_RAW_SIZE)}.",
            )

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        # Validate PDF magic bytes
        header = file_bytes[:5].decode("ascii", errors="replace")
        if not header.startswith("%PDF-"):
            return PDFError(
                reason="corrupted",
                message=f"File is not a valid PDF (missing %PDF- header): {file_path}",
            )

        b64 = base64.b64encode(file_bytes).decode("ascii")

        return PDFSuccess(
            type="pdf",
            file=PDFFileData(
                file_path=file_path,
                base64_data=b64,
                original_size=original_size,
            ),
        )
    except Exception as e:
        return PDFError(reason="unknown", message=str(e))


async def get_pdf_page_count(file_path: str) -> Optional[int]:
    """Get the number of pages in a PDF using pdfinfo (poppler-utils)."""
    try:
        result = subprocess.run(
            ["pdfinfo", file_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        import re
        match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
        if match:
            return int(match.group(1))
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


_pdftoppm_available: Optional[bool] = None


def reset_pdftoppm_cache() -> None:
    """Reset the pdftoppm availability cache."""
    global _pdftoppm_available
    _pdftoppm_available = None


async def is_pdftoppm_available() -> bool:
    """Check whether pdftoppm (poppler-utils) is available."""
    global _pdftoppm_available
    if _pdftoppm_available is not None:
        return _pdftoppm_available
    try:
        result = subprocess.run(
            ["pdftoppm", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _pdftoppm_available = result.returncode == 0 or len(result.stderr) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _pdftoppm_available = False
    return _pdftoppm_available


async def extract_pdf_pages(
    file_path: str,
    output_base_dir: str,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
) -> Union[PDFExtractSuccess, PDFError]:
    """Extract PDF pages as JPEG images using pdftoppm."""
    try:
        stat = os.stat(file_path)
        original_size = stat.st_size

        if original_size == 0:
            return PDFError(reason="empty", message=f"PDF file is empty: {file_path}")

        if original_size > PDF_MAX_EXTRACT_SIZE:
            return PDFError(
                reason="too_large",
                message=f"PDF file exceeds maximum allowed size for extraction ({_format_file_size(PDF_MAX_EXTRACT_SIZE)}).",
            )

        if not await is_pdftoppm_available():
            return PDFError(
                reason="unavailable",
                message=(
                    "pdftoppm is not installed. Install poppler-utils "
                    "(e.g. `brew install poppler` or `apt-get install poppler-utils`) "
                    "to enable PDF page rendering."
                ),
            )

        output_dir = os.path.join(output_base_dir, f"pdf-{uuid.uuid4()}")
        os.makedirs(output_dir, exist_ok=True)

        prefix = os.path.join(output_dir, "page")
        args = ["pdftoppm", "-jpeg", "-r", "100"]
        if first_page is not None:
            args.extend(["-f", str(first_page)])
        if last_page is not None and last_page != float("inf"):
            args.extend(["-l", str(last_page)])
        args.extend([file_path, prefix])

        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            stderr = result.stderr
            if "password" in stderr.lower():
                return PDFError(
                    reason="password_protected",
                    message="PDF is password-protected. Please provide an unprotected version.",
                )
            if any(w in stderr.lower() for w in ("damaged", "corrupt", "invalid")):
                return PDFError(
                    reason="corrupted",
                    message="PDF file is corrupted or invalid.",
                )
            return PDFError(reason="unknown", message=f"pdftoppm failed: {stderr}")

        image_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".jpg"))
        if not image_files:
            return PDFError(
                reason="corrupted",
                message="pdftoppm produced no output pages. The PDF may be invalid.",
            )

        return PDFExtractSuccess(
            type="parts",
            file=PDFExtractData(
                file_path=file_path,
                original_size=original_size,
                count=len(image_files),
                output_dir=output_dir,
            ),
        )
    except Exception as e:
        return PDFError(reason="unknown", message=str(e))

"""MCP output storage: format descriptions, large output instructions, binary persistence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


def get_format_description(type_: str, schema: object = None) -> str:
    """Generate a format description string based on MCP result type and schema."""
    if type_ == "toolResult":
        return "Plain text"
    elif type_ == "structuredContent":
        return f"JSON with schema: {schema}" if schema else "JSON"
    elif type_ == "contentArray":
        return f"JSON array with schema: {schema}" if schema else "JSON array"
    return "Unknown"


def get_large_output_instructions(
    raw_output_path: str,
    content_length: int,
    format_description: str,
    max_read_length: Optional[int] = None,
) -> str:
    """Generate instruction text for reading from a saved output file."""
    base = (
        f"Error: result ({content_length:,} characters) exceeds maximum allowed tokens. "
        f"Output has been saved to {raw_output_path}.\n"
        f"Format: {format_description}\n"
        f"Use offset and limit parameters to read specific portions of the file, "
        f"search within it for specific content, and jq to make structured queries.\n"
        f"REQUIREMENTS FOR SUMMARIZATION/ANALYSIS/REVIEW:\n"
        f"- You MUST read the content from the file at {raw_output_path} in sequential "
        f"chunks until 100% of the content has been read.\n"
    )

    if max_read_length:
        truncation_warning = (
            f"- If you receive truncation warnings when reading the file "
            f'("[N lines truncated]"), reduce the chunk size until you have read '
            f"100% of the content without truncation "
            f"***DO NOT PROCEED UNTIL YOU HAVE DONE THIS***. "
            f"Bash output is limited to {max_read_length:,} chars.\n"
        )
    else:
        truncation_warning = (
            "- If you receive truncation warnings when reading the file, "
            "reduce the chunk size until you have read 100% of the content "
            "without truncation.\n"
        )

    completion_req = (
        "- Before producing ANY summary or analysis, you MUST explicitly describe "
        "what portion of the content you have read. ***If you did not read the entire "
        "content, you MUST explicitly state this.***\n"
    )

    return base + truncation_warning + completion_req


# Mime type to file extension mapping
_MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "application/json": "json",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/html": "html",
    "text/markdown": "md",
    "application/zip": "zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def extension_for_mime_type(mime_type: Optional[str]) -> str:
    """Map a MIME type to a file extension."""
    if not mime_type:
        return "bin"
    mt = mime_type.split(";")[0].strip().lower()
    return _MIME_EXTENSIONS.get(mt, "bin")


def is_binary_content_type(content_type: str) -> bool:
    """Heuristic for whether a content-type indicates binary content."""
    if not content_type:
        return False
    mt = content_type.split(";")[0].strip().lower()
    if mt.startswith("text/"):
        return False
    if mt.endswith("+json") or mt == "application/json":
        return False
    if mt.endswith("+xml") or mt == "application/xml":
        return False
    if mt.startswith("application/javascript"):
        return False
    if mt == "application/x-www-form-urlencoded":
        return False
    return True


@dataclass
class PersistBinarySuccess:
    filepath: str
    size: int
    ext: str


@dataclass
class PersistBinaryError:
    error: str


PersistBinaryResult = Union[PersistBinarySuccess, PersistBinaryError]


async def persist_binary_content(
    data: bytes,
    mime_type: Optional[str],
    persist_id: str,
    output_dir: str,
) -> PersistBinaryResult:
    """Write raw binary bytes to an output directory with mime-derived extension."""
    ext = extension_for_mime_type(mime_type)
    filepath = os.path.join(output_dir, f"{persist_id}.{ext}")

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(data)
    except Exception as e:
        return PersistBinaryError(error=str(e))

    return PersistBinarySuccess(filepath=filepath, size=len(data), ext=ext)


def get_binary_blob_saved_message(
    filepath: str,
    mime_type: Optional[str],
    size: int,
    source_description: str,
) -> str:
    """Build a short message telling where binary content was saved."""
    mt = mime_type or "unknown type"
    size_str = _format_file_size(size)
    return f"{source_description}Binary content ({mt}, {size_str}) saved to {filepath}"


def _format_file_size(size: int) -> str:
    """Format a file size in human-readable form."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

"""Jupyter notebook reading and processing utilities."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

LARGE_OUTPUT_THRESHOLD = 10000


@dataclass
class NotebookOutputImage:
    image_data: str
    media_type: str


@dataclass
class NotebookCellSourceOutput:
    output_type: str
    text: str = ""
    image: Optional[NotebookOutputImage] = None


@dataclass
class NotebookCellSource:
    cell_type: str
    source: str
    cell_id: str
    language: Optional[str] = None
    execution_count: Optional[int] = None
    outputs: Optional[list[NotebookCellSourceOutput]] = None


def _is_large_outputs(outputs: list[Optional[NotebookCellSourceOutput]]) -> bool:
    size = 0
    for o in outputs:
        if o is None:
            continue
        size += len(o.text or "")
        if o.image:
            size += len(o.image.image_data)
        if size > LARGE_OUTPUT_THRESHOLD:
            return True
    return False


def _truncate_output(text: str, max_length: int = 16000) -> str:
    """Truncate output text if too long."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"\n[truncated {len(text) - max_length} chars]"


def _process_output_text(text: Any) -> str:
    if not text:
        return ""
    if isinstance(text, list):
        raw = "".join(text)
    else:
        raw = str(text)
    return _truncate_output(raw)


def _extract_image(data: dict) -> Optional[NotebookOutputImage]:
    if isinstance(data.get("image/png"), str):
        return NotebookOutputImage(
            image_data=data["image/png"].replace(" ", "").replace("\n", ""),
            media_type="image/png",
        )
    if isinstance(data.get("image/jpeg"), str):
        return NotebookOutputImage(
            image_data=data["image/jpeg"].replace(" ", "").replace("\n", ""),
            media_type="image/jpeg",
        )
    return None


def _process_output(output: dict) -> Optional[NotebookCellSourceOutput]:
    output_type = output.get("output_type", "")

    if output_type == "stream":
        return NotebookCellSourceOutput(
            output_type=output_type,
            text=_process_output_text(output.get("text")),
        )
    elif output_type in ("execute_result", "display_data"):
        data = output.get("data", {})
        return NotebookCellSourceOutput(
            output_type=output_type,
            text=_process_output_text(data.get("text/plain")) if data else "",
            image=_extract_image(data) if data else None,
        )
    elif output_type == "error":
        traceback = output.get("traceback", [])
        error_text = (
            f"{output.get('ename', 'Error')}: {output.get('evalue', '')}\n"
            + "\n".join(traceback)
        )
        return NotebookCellSourceOutput(
            output_type=output_type,
            text=_process_output_text(error_text),
        )
    return None


def _process_cell(
    cell: dict,
    index: int,
    code_language: str,
    include_large_outputs: bool,
) -> NotebookCellSource:
    cell_id = cell.get("id") or f"cell-{index}"
    cell_type = cell.get("cell_type", "code")
    source = cell.get("source", "")
    if isinstance(source, list):
        source = "".join(source)

    cell_data = NotebookCellSource(
        cell_type=cell_type,
        source=source,
        cell_id=cell_id,
        execution_count=(
            cell.get("execution_count") if cell_type == "code" else None
        ),
    )

    if cell_type == "code":
        cell_data.language = code_language

    if cell_type == "code" and cell.get("outputs"):
        outputs = [_process_output(o) for o in cell["outputs"]]
        if not include_large_outputs and _is_large_outputs(outputs):
            cell_data.outputs = [
                NotebookCellSourceOutput(
                    output_type="stream",
                    text=(
                        f"Outputs are too large to include. Use bash with: "
                        f"cat <notebook_path> | jq '.cells[{index}].outputs'"
                    ),
                )
            ]
        else:
            cell_data.outputs = [o for o in outputs if o is not None]

    return cell_data


async def read_notebook(
    notebook_path: str,
    cell_id: Optional[str] = None,
) -> list[NotebookCellSource]:
    """Read and parse a Jupyter notebook file into processed cell data."""
    full_path = os.path.expanduser(notebook_path)
    with open(full_path, "r", encoding="utf-8") as f:
        notebook = json.load(f)

    language = (
        notebook.get("metadata", {})
        .get("language_info", {})
        .get("name", "python")
    )

    cells = notebook.get("cells", [])

    if cell_id:
        for i, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return [_process_cell(cell, i, language, True)]
        raise ValueError(f'Cell with ID "{cell_id}" not found in notebook')

    return [_process_cell(cell, i, language, False) for i, cell in enumerate(cells)]


def cell_content_to_text(cell: NotebookCellSource) -> str:
    """Convert a notebook cell to text representation."""
    metadata_parts: list[str] = []
    if cell.cell_type != "code":
        metadata_parts.append(f"<cell_type>{cell.cell_type}</cell_type>")
    if cell.language and cell.language != "python" and cell.cell_type == "code":
        metadata_parts.append(f"<language>{cell.language}</language>")

    metadata = "".join(metadata_parts)
    return f'<cell id="{cell.cell_id}">{metadata}{cell.source}</cell id="{cell.cell_id}">'


def parse_cell_id(cell_id: str) -> Optional[int]:
    """Parse a cell-N style ID to an integer index."""
    import re

    match = re.match(r"^cell-(\d+)$", cell_id)
    if match:
        return int(match.group(1))
    return None

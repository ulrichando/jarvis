"""Prompt for the NotebookEditTool."""
from __future__ import annotations

from src.tools.NotebookEditTool.constants import NOTEBOOK_EDIT_TOOL_NAME

DESCRIPTION = "Edit Jupyter notebook (.ipynb) cells"

PROMPT = f"""Edit Jupyter notebook (.ipynb) cells. Use this tool to modify cells in a notebook.

## Usage
- **edit_cell**: Modify the source of an existing cell
- **add_cell**: Add a new cell at a specific position
- **delete_cell**: Remove a cell at a specific position

## Parameters
- notebook_path: Path to the .ipynb file
- cell_index: Zero-based index of the cell to edit (for edit_cell and delete_cell)
- action: "edit_cell", "add_cell", or "delete_cell"
- new_source: The new cell content (for edit_cell and add_cell)
- cell_type: "code" or "markdown" (for add_cell, defaults to "code")
"""

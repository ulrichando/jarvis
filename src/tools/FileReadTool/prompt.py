from __future__ import annotations

from src.tools.BashTool.toolName import BASH_TOOL_NAME

# Use a string constant for tool names to avoid circular dependencies
FILE_READ_TOOL_NAME = "read_file"

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier Read tool_result "
    "in this conversation is still current \u2014 refer to that instead of re-reading."
)

MAX_LINES_TO_READ = 2000

DESCRIPTION = "Read a file from the local filesystem."

LINE_FORMAT_INSTRUCTION = (
    "- Results are returned using cat -n format, with line numbers starting at 1"
)

OFFSET_INSTRUCTION_DEFAULT = (
    "- You can optionally specify a line offset and limit (especially handy for long "
    "files), but it's recommended to read the whole file by not providing these parameters"
)

OFFSET_INSTRUCTION_TARGETED = (
    "- When you already know which part of the file you need, only read that part. "
    "This can be important for larger files."
)


def render_prompt_template(
    line_format: str,
    max_size_instruction: str,
    offset_instruction: str,
    pdf_supported: bool = False,
) -> str:
    """Renders the Read tool prompt template.

    The caller (FileReadTool) supplies the runtime-computed parts.
    """
    pdf_section = ""
    if pdf_supported:
        pdf_section = (
            '\n- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), '
            'you MUST provide the pages parameter to read specific page ranges (e.g., '
            'pages: "1-5"). Reading a large PDF without the pages parameter will fail. '
            'Maximum 20 pages per request.'
        )

    return (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to "
        "a file assume that path is valid. It is okay to read a file that does not exist; an error "
        "will be returned.\n"
        "\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        f"- By default, it reads up to {MAX_LINES_TO_READ} lines starting from the beginning "
        f"of the file{max_size_instruction}\n"
        f"{offset_instruction}\n"
        f"{line_format}\n"
        "- This tool allows JARVIS to read images (eg PNG, JPG, etc). When reading an image "
        f"file the contents are presented visually as JARVIS is a multimodal LLM."
        f"{pdf_section}\n"
        "- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their "
        "outputs, combining code, text, and visualizations.\n"
        f"- This tool can only read files, not directories. To read a directory, use an ls command "
        f"via the {BASH_TOOL_NAME} tool.\n"
        "- You will regularly be asked to read screenshots. If the user provides a path to a "
        "screenshot, ALWAYS use this tool to view the file at the path. This tool will work with "
        "all temporary file paths.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder "
        "warning in place of file contents."
    )

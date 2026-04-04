# In its own file to avoid circular dependencies
FILE_EDIT_TOOL_NAME = "Edit"

# Permission pattern for granting session-level access to the project's .claude/ folder
CLAUDE_FOLDER_PERMISSION_PATTERN = "/.claude/**"

# Permission pattern for granting session-level access to the global ~/.claude/ folder
GLOBAL_CLAUDE_FOLDER_PERMISSION_PATTERN = "~/.claude/**"

FILE_UNEXPECTEDLY_MODIFIED_ERROR = (
    "File has been unexpectedly modified. Read it again before attempting to write it."
)

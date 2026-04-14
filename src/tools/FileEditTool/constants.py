# In its own file to avoid circular dependencies
FILE_EDIT_TOOL_NAME = "edit_file"

# Permission pattern for granting session-level access to the project's .jarvis/ folder
JARVIS_FOLDER_PERMISSION_PATTERN = "/.jarvis/**"

# Permission pattern for granting session-level access to the global ~/.jarvis/ folder
GLOBAL_JARVIS_FOLDER_PERMISSION_PATTERN = "~/.jarvis/**"

FILE_UNEXPECTEDLY_MODIFIED_ERROR = (
    "File has been unexpectedly modified. Read it again before attempting to write it."
)

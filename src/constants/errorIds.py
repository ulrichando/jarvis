"""
Error IDs for tracking error sources in production.
These IDs are obfuscated identifiers that help us trace
which logError() call generated an error.

ADDING A NEW ERROR TYPE:
1. Add a constant based on Next ID.
2. Increment Next ID.
Next ID: 346
"""

E_TOOL_USE_SUMMARY_GENERATION_FAILED: int = 344

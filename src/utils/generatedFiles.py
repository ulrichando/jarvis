"""
Generated file detection.

Identifies files that should be excluded from attribution based on
Linguist-style rules (lock files, build artifacts, vendored code, etc.).
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

# Exact file name matches (case-insensitive)
EXCLUDED_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "bun.lock",
    "composer.lock",
    "gemfile.lock",
    "cargo.lock",
    "poetry.lock",
    "pipfile.lock",
    "shrinkwrap.json",
    "npm-shrinkwrap.json",
}

# File extension patterns (case-insensitive)
EXCLUDED_EXTENSIONS = {
    ".lock",
    ".min.js",
    ".min.css",
    ".min.html",
    ".bundle.js",
    ".bundle.css",
    ".generated.ts",
    ".generated.js",
    ".d.ts",
}

# Directory patterns that indicate generated/vendored content
EXCLUDED_DIRECTORIES = [
    "/dist/",
    "/build/",
    "/out/",
    "/output/",
    "/node_modules/",
    "/vendor/",
    "/vendored/",
    "/third_party/",
    "/third-party/",
    "/external/",
    "/.next/",
    "/.nuxt/",
    "/.svelte-kit/",
    "/coverage/",
    "/__pycache__/",
    "/.tox/",
    "/venv/",
    "/.venv/",
    "/target/release/",
    "/target/debug/",
]

# Filename patterns using regex for more complex matching
EXCLUDED_FILENAME_PATTERNS = [
    re.compile(r"^.*\.min\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*-min\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.bundle\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.generated\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.gen\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.auto\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*_generated\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*_gen\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.pb\.(go|js|ts|py|rb)$", re.IGNORECASE),
    re.compile(r"^.*_pb2?\.py$", re.IGNORECASE),
    re.compile(r"^.*\.pb\.h$", re.IGNORECASE),
    re.compile(r"^.*\.grpc\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.swagger\.[a-z]+$", re.IGNORECASE),
    re.compile(r"^.*\.openapi\.[a-z]+$", re.IGNORECASE),
]


def is_generated_file(file_path: str) -> bool:
    """
    Check if a file should be excluded from attribution based on
    Linguist-style rules.

    Args:
        file_path: Relative file path from repository root.

    Returns:
        True if the file should be excluded from attribution.
    """
    # Normalize to posix-style path for pattern matching
    normalized_path = "/" + file_path.replace(os.sep, "/").lstrip("/")
    file_name = os.path.basename(file_path).lower()
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    # Check exact filename matches
    if file_name in EXCLUDED_FILENAMES:
        return True

    # Check extension matches
    if ext in EXCLUDED_EXTENSIONS:
        return True

    # Check for compound extensions like .min.js
    parts = file_name.split(".")
    if len(parts) > 2:
        compound_ext = "." + ".".join(parts[-2:])
        if compound_ext in EXCLUDED_EXTENSIONS:
            return True

    # Check directory patterns
    for dir_pattern in EXCLUDED_DIRECTORIES:
        if dir_pattern in normalized_path:
            return True

    # Check filename patterns
    for pattern in EXCLUDED_FILENAME_PATTERNS:
        if pattern.match(file_name):
            return True

    return False


def filter_generated_files(files: list[str]) -> list[str]:
    """
    Filter a list of files to exclude generated files.

    Args:
        files: List of file paths.

    Returns:
        List of files that are not generated.
    """
    return [f for f in files if not is_generated_file(f)]

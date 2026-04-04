"""Patch optimizer - merges and deduplicates render patches."""

from __future__ import annotations

from typing import Any

from .frame import Diff, Patch


def optimize(diff: Diff) -> Diff:
    """Optimize a diff by merging adjacent stdout patches.

    Consecutive stdout patches are joined into a single patch to reduce
    the number of write() calls to the terminal.
    """
    if not diff:
        return diff

    result: Diff = []
    i = 0

    while i < len(diff):
        patch = diff[i]
        if patch.type == "stdout":
            # Merge consecutive stdout patches
            merged = patch.content
            j = i + 1
            while j < len(diff) and diff[j].type == "stdout":
                merged += diff[j].content
                j += 1
            result.append(Patch(type="stdout", content=merged))
            i = j
        else:
            result.append(patch)
            i += 1

    return result

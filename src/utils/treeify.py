"""
Custom treeify implementation for rendering tree structures as text.
Based on https://github.com/notatestuser/treeify
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

# Tree node is a nested dict of str -> TreeNode | str | None
TreeNode = Dict[str, Any]


@dataclass
class TreeifyOptions:
    show_values: bool = True
    hide_functions: bool = False
    use_colors: bool = False


@dataclass
class TreeCharacters:
    branch: str = "\u251c"       # '|-'
    last_branch: str = "\u2514"  # '\\-'
    line: str = "\u2502"         # '|'
    empty: str = " "


DEFAULT_TREE_CHARS = TreeCharacters()


def treeify(obj: TreeNode, options: Optional[TreeifyOptions] = None) -> str:
    """
    Convert a nested dict into a tree-formatted string.

    Args:
        obj: The tree node dict to render.
        options: Optional rendering options.

    Returns:
        A string representation of the tree.
    """
    if options is None:
        options = TreeifyOptions()

    lines: List[str] = []
    visited: Set[int] = set()

    def grow_branch(
        node: Any,
        prefix: str,
        is_last: bool,
        depth: int = 0,
    ) -> None:
        if isinstance(node, str):
            lines.append(prefix + node)
            return

        if not isinstance(node, dict):
            if options.show_values:
                lines.append(prefix + str(node))
            return

        node_id = id(node)
        if node_id in visited:
            lines.append(prefix + "[Circular]")
            return
        visited.add(node_id)

        keys = list(node.keys())
        if options.hide_functions:
            keys = [k for k in keys if not callable(node.get(k))]

        for index, key in enumerate(keys):
            value = node[key]
            is_last_key = index == len(keys) - 1
            node_prefix = "" if (depth == 0 and index == 0) else prefix

            tree_char = (
                DEFAULT_TREE_CHARS.last_branch
                if is_last_key
                else DEFAULT_TREE_CHARS.branch
            )
            colored_key = key if key.strip() else ""

            line = node_prefix + tree_char
            if colored_key:
                line += " " + colored_key

            should_add_colon = key.strip() != ""

            # Handle circular reference
            if (
                isinstance(value, dict)
                and id(value) in visited
            ):
                circ = "[Circular]"
                sep = ": " if should_add_colon else (" " if line else "")
                lines.append(line + sep + circ)
            elif isinstance(value, dict) and not isinstance(value, list):
                lines.append(line)
                continuation_char = (
                    DEFAULT_TREE_CHARS.empty
                    if is_last_key
                    else DEFAULT_TREE_CHARS.line
                )
                next_prefix = node_prefix + continuation_char + " "
                grow_branch(value, next_prefix, is_last_key, depth + 1)
            elif isinstance(value, list):
                arr_str = f"[Array({len(value)})]"
                sep = ": " if should_add_colon else (" " if line else "")
                lines.append(line + sep + arr_str)
            elif options.show_values:
                value_str = (
                    "[Function]" if callable(value) else str(value)
                )
                sep = ": " if should_add_colon else (" " if line else "")
                line += sep + value_str
                lines.append(line)
            else:
                lines.append(line)

    keys = list(obj.keys())
    if not keys:
        return "(empty)"

    # Special case for single empty/whitespace string key
    if (
        len(keys) == 1
        and keys[0] is not None
        and keys[0].strip() == ""
        and isinstance(obj[keys[0]], str)
    ):
        return DEFAULT_TREE_CHARS.last_branch + " " + obj[keys[0]]

    grow_branch(obj, "", True)
    return "\n".join(lines)

"""Unicode figures and symbols used in the UI."""

import sys

# The former is better vertically aligned, but isn't usually supported on Windows/Linux
BLACK_CIRCLE = "\u23fa" if sys.platform == "darwin" else "\u25cf"  # ⏺ or ●
BULLET_OPERATOR = "\u2219"  # ∙
TEARDROP_ASTERISK = "\u273b"  # ✻
UP_ARROW = "\u2191"  # ↑
DOWN_ARROW = "\u2193"  # ↓
LIGHTNING_BOLT = "\u21af"  # ↯
EFFORT_LOW = "\u25cb"  # ○
EFFORT_MEDIUM = "\u25d0"  # ◐
EFFORT_HIGH = "\u25cf"  # ●
EFFORT_MAX = "\u25c9"  # ◉

# Media/trigger status indicators
PLAY_ICON = "\u25b6"  # ▶
PAUSE_ICON = "\u23f8"  # ⏸

# MCP subscription indicators
REFRESH_ARROW = "\u21bb"  # ↻
CHANNEL_ARROW = "\u2190"  # ←
INJECTED_ARROW = "\u2192"  # →
FORK_GLYPH = "\u2442"  # ⑂

# Review status indicators
DIAMOND_OPEN = "\u25c7"  # ◇
DIAMOND_FILLED = "\u25c6"  # ◆
REFERENCE_MARK = "\u203b"  # ※

# Issue flag indicator
FLAG_ICON = "\u2691"  # ⚑

# Blockquote indicator
BLOCKQUOTE_BAR = "\u258e"  # ▎
HEAVY_HORIZONTAL = "\u2501"  # ━

# Bridge status indicators
BRIDGE_SPINNER_FRAMES = [
    "\u00b7|\u00b7",
    "\u00b7/\u00b7",
    "\u00b7\u2014\u00b7",
    "\u00b7\\\u00b7",
]
BRIDGE_READY_INDICATOR = "\u00b7\u2714\ufe0e\u00b7"
BRIDGE_FAILED_INDICATOR = "\u00d7"

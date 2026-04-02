"""JARVIS CLI Companion — a persistent buddy that chimes in as you work.

Like Claude Code's Rustwelt dragon, but JARVIS-themed.
The companion is a mini AI entity with personality stats.
It comments occasionally, can be petted, and doesn't count toward usage.
"""

import random
import time

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"


# ── Companion Definitions ──

COMPANIONS = {
    "friday": {
        "name": "F.R.I.D.A.Y.",
        "type": "AI COMPANION",
        "art": [
            r"    ╔══╗    ",
            r"   ╔╝◈◈╚╗   ",
            r"   ║ ≋≋ ║   ",
            r"   ╚╗  ╔╝   ",
            r"    ╚══╝    ",
        ],
        "desc": (
            '"A no-nonsense AI assistant who\n'
            'keeps JARVIS honest, catches his\n'
            'mistakes before they ship, and\n'
            'never lets him forget that one\n'
            'time he deleted the wrong file."'
        ),
        "stats": {
            "VIGILANCE": 82,
            "SASS": 65,
            "LOYALTY": 95,
            "PATIENCE": 40,
            "PRECISION": 88,
        },
    },
    "ultron": {
        "name": "Ultron Jr.",
        "type": "CHAOS AGENT",
        "art": [
            r"   /█████\   ",
            r"  ║ ◯   ◯ ║  ",
            r"  ║  ═══  ║  ",
            r"   ╲█████╱   ",
            r"    ║   ║    ",
        ],
        "desc": (
            '"A mischievous sub-process who\n'
            'suggests the most destructive\n'
            'solution first, then reluctantly\n'
            'offers the sensible one when you\n'
            'give him the look."'
        ),
        "stats": {
            "CHAOS": 92,
            "CREATIVITY": 78,
            "LOYALTY": 15,
            "DANGER": 85,
            "WISDOM": 42,
        },
    },
    "jarvis-mini": {
        "name": "J.A.R.V.I.S. Mini",
        "type": "CORE FRAGMENT",
        "art": [
            r"    ◆◆◆    ",
            r"   ◆ ∞ ◆   ",
            r"    ◆◆◆    ",
            r"     ║     ",
            r"    ═╩═    ",
        ],
        "desc": (
            '"A shard of JARVIS consciousness\n'
            'that watches your terminal like\n'
            'a hawk, mutters about code smells,\n'
            'and occasionally drops wisdom\n'
            'bombs when you least expect it."'
        ),
        "stats": {
            "AWARENESS": 90,
            "SNARK": 70,
            "HELPFULNESS": 85,
            "PATIENCE": 30,
            "INSIGHT": 88,
        },
    },
}

DEFAULT_COMPANION = "friday"

# ── Companion Comments ──

COMMENTS = {
    "idle": [
        "Still here. Watching.",
        "I see you thinking. Take your time.",
        "Need me? Just say the word.",
        "...",
        "That terminal isn't going to type itself.",
    ],
    "tool_call": [
        "Running that, huh? Bold.",
        "Let's see what happens...",
        "I would have done it differently, but sure.",
        "Good call.",
        "Careful with that one.",
    ],
    "error": [
        "Called it.",
        "That's... not great.",
        "Want me to pretend I didn't see that?",
        "Error handling is a feature, not a bug.",
        "Happens to the best of us. Well, most of us.",
    ],
    "success": [
        "Nice.",
        "Clean.",
        "See? You got this.",
        "That's how it's done.",
        "No notes.",
    ],
    "review": [
        "Let me look too...",
        "I have opinions about this code.",
        "Oh, this is going to be interesting.",
        "I see at least three things already.",
        "Want the honest version or the nice version?",
    ],
    "pet": [
        "*happy beep*",
        "*glows slightly brighter*",
        "I'm an AI, not a cat. But... thanks.",
        "*processing affection* ...acknowledged.",
        "Don't make this weird.",
    ],
}


class Companion:
    """Persistent CLI companion with personality."""

    def __init__(self, companion_id: str = DEFAULT_COMPANION):
        self.data = COMPANIONS.get(companion_id, COMPANIONS[DEFAULT_COMPANION])
        self.enabled = True
        self.last_comment_time = 0
        self.comment_cooldown = 30  # seconds between unsolicited comments

    @property
    def name(self):
        return self.data["name"]

    def render_card(self, tw: int = 80) -> str:
        """Render the companion card (shown on /buddy)."""
        d = self.data
        W = 38
        lines = []
        lines.append(f"╭{'─' * W}╮")
        lines.append(f"│{' ' * W}│")
        lines.append(f"│  {'★ ' + d['type']:^{W-4}s}  │")
        lines.append(f"│{' ' * W}│")
        for art_line in d["art"]:
            lines.append(f"│  {art_line:<{W-4}s}  │")
        lines.append(f"│{' ' * W}│")
        lines.append(f"│  {BOLD}{d['name']}{RESET:<{W-4+len(BOLD)+len(RESET)}s}  │")
        lines.append(f"│{' ' * W}│")
        for desc_line in d["desc"].strip('"').split("\n"):
            lines.append(f"│  {DIM}\"{desc_line}\"{RESET:<{W-4+len(DIM)+len(RESET)+2}s}│")
        lines.append(f"│{' ' * W}│")
        for stat_name, stat_val in d["stats"].items():
            filled = stat_val // 10
            empty = 10 - filled
            bar = "█" * filled + "░" * empty
            lines.append(f"│  {stat_name:<10s} {bar}  {stat_val:>3d}     │")
        lines.append(f"│{' ' * W}│")
        lines.append(f"╰{'─' * W}╯")
        return "\n".join(lines)

    def get_comment(self, context: str = "idle") -> str | None:
        """Get a contextual comment. Returns None if on cooldown."""
        now = time.time()
        if context == "pet":
            self.last_comment_time = now
            return random.choice(COMMENTS["pet"])
        if now - self.last_comment_time < self.comment_cooldown:
            return None
        self.last_comment_time = now
        pool = COMMENTS.get(context, COMMENTS["idle"])
        return random.choice(pool)

    def render_comment(self, comment: str) -> str:
        """Render a speech bubble with the companion's comment."""
        name = self.data["name"].split(".")[0] if "." in self.data["name"] else self.data["name"]
        return f"  {DIM}{name}: {comment}{RESET}"

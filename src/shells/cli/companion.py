"""JARVIS CLI Companion — a persistent buddy that chimes in as you work.

A JARVIS-themed persistent companion.
The companion is a mini AI entity with personality stats.
It comments occasionally, can be petted, and doesn't count toward usage.
"""

import os
import random
import time
from dataclasses import dataclass, field

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"


# ── Seeded PRNG for deterministic companion generation ──

def _mulberry32(seed: int):
    """Mulberry32 PRNG -- deterministic per-seed."""
    a = seed & 0xffffffff
    def rng():
        nonlocal a
        a = (a + 0x6d2b79f5) & 0xffffffff
        t = (a ^ (a >> 15)) * (1 | a) & 0xffffffff
        t = (t + ((t ^ (t >> 7)) * (61 | t))) & 0xffffffff
        return ((t ^ (t >> 14)) & 0xffffffff) / 0x100000000
    return rng

def _fnv1a_hash(s: str) -> int:
    """FNV-1a hash for string -> int."""
    h = 2166136261
    for c in s:
        h ^= ord(c)
        h = (h * 16777619) & 0xffffffff
    return h


# ── Species, rarities, and customization constants ──

SPECIES = [
    "dragon", "cat", "owl", "penguin", "ghost", "robot", "octopus",
    "turtle", "fox", "wolf", "phoenix", "golem", "sprite", "raven",
    "serpent", "bear", "hawk", "panther",
]

RARITIES = ["common", "uncommon", "rare", "epic", "legendary"]
RARITY_WEIGHTS = [60, 25, 10, 4, 1]
RARITY_STARS = {"common": "★", "uncommon": "★★", "rare": "★★★", "epic": "★★★★", "legendary": "★★★★★"}
RARITY_STAT_FLOORS = {"common": 5, "uncommon": 15, "rare": 25, "epic": 35, "legendary": 50}

EYES = ["·", "✦", "×", "◉", "@", "°"]
HATS = ["none", "crown", "tophat", "halo", "wizard", "beanie", "horns", "antenna"]

STAT_NAMES = ["HACKING", "PATIENCE", "CHAOS", "WISDOM", "SNARK"]


# ── Dataclasses ──

@dataclass
class CompanionBones:
    """Deterministic visual/stat attributes -- regenerated from user hash."""
    rarity: str = "common"
    species: str = "dragon"
    eye: str = "·"
    hat: str = "none"
    shiny: bool = False
    stats: dict = field(default_factory=dict)

@dataclass
class CompanionSoul:
    """Persistent personality -- stored in config."""
    name: str = ""
    personality: str = ""
    hatched_at: float = 0.0


# ── Generation functions ──

def _pick(rng, arr):
    return arr[int(rng() * len(arr))]

def _roll_rarity(rng):
    total = sum(RARITY_WEIGHTS)
    roll = rng() * total
    for i, r in enumerate(RARITIES):
        roll -= RARITY_WEIGHTS[i]
        if roll < 0:
            return r
    return RARITIES[-1]

def _roll_stats(rng, rarity):
    floor = RARITY_STAT_FLOORS[rarity]
    stats = {}
    indices = list(range(len(STAT_NAMES)))
    peak = int(rng() * len(indices))
    dump = (peak + 1 + int(rng() * (len(indices) - 1))) % len(indices)
    for i, name in enumerate(STAT_NAMES):
        if i == peak:
            stats[name] = floor + 50 + int(rng() * 30)
        elif i == dump:
            stats[name] = max(1, floor - 10 + int(rng() * 15))
        else:
            stats[name] = floor + int(rng() * 40)
    return stats

def generate_companion(user_id: str = "") -> tuple[CompanionBones, int]:
    """Generate deterministic companion from user ID."""
    if not user_id:
        user_id = os.environ.get("USER", "jarvis-user")
    seed = _fnv1a_hash(user_id + "jarvis-companion-2026")
    rng = _mulberry32(seed)

    rarity = _roll_rarity(rng)
    bones = CompanionBones(
        rarity=rarity,
        species=_pick(rng, SPECIES),
        eye=_pick(rng, EYES),
        hat=_pick(rng, HATS),
        shiny=rng() < 0.01,
        stats=_roll_stats(rng, rarity),
    )
    return bones, seed


# ── ASCII Sprite Database ──
# Each species has 3 frames: idle, sway, blink. {E} = eye placeholder.

SPRITES = {
    "dragon": [
        "  /\\_/\\  \n ({E}  {E}) \n / >  < \\\n|  ~~   |\n \\____/ ",
        "  /\\_/\\  \n ({E}  {E}) \n / >  < \\\n|  ~    |\n \\____/ ",
        "  /\\_/\\  \n (-  -) \n / >  < \\\n|  ~~   |\n \\____/ ",
    ],
    "cat": [
        " /\\_/\\  \n( {E} {E} ) \n > ^ <  \n  / \\   \n _| |_  ",
        " /\\_/\\  \n( {E} {E} ) \n > ^ <  \n  | |   \n _| |_  ",
        " /\\_/\\  \n( - - ) \n > ^ <  \n  / \\   \n _| |_  ",
    ],
    "owl": [
        " {{\\_/}} \n(( {E} {E} ))\n  ( > ) \n  /| |\\\n _| |_ ",
        " {{\\_/}} \n(( {E} {E} ))\n  ( > ) \n  \\| |/\n _| |_ ",
        " {{\\_/}} \n(( - - ))\n  ( > ) \n  /| |\\\n _| |_ ",
    ],
    "penguin": [
        "  (^^)  \n /({E}{E})\\\n( >  < )\n \\    / \n  |  |  ",
        "  (^^)  \n /({E}{E})\\\n( >  < )\n  \\  /  \n  |  |  ",
        "  (^^)  \n /(--)\\\n( >  < )\n \\    / \n  |  |  ",
    ],
    "ghost": [
        "  .---.  \n / {E} {E} \\\n|  o  |\n|     |\n ^^^^\\ ",
        "  .---.  \n / {E} {E} \\\n|  o  |\n|     |\n /^^^^ ",
        "  .---.  \n / - - \\\n|  o  |\n|     |\n ^^^^\\ ",
    ],
    "robot": [
        " [====] \n |{E}  {E}| \n |_[]_| \n  /||\\\\\n _|  |_ ",
        " [====] \n |{E}  {E}| \n |_[]_| \n  \\\\||/\n _|  |_ ",
        " [====] \n |_  _| \n |_[]_| \n  /||\\\\\n _|  |_ ",
    ],
    "octopus": [
        "  .~~~.  \n ( {E} {E} ) \n  \\_^_/ \n /|/|\\|\\\n~ ~ ~ ~ ",
        "  .~~~.  \n ( {E} {E} ) \n  \\_^_/ \n\\|\\|/|/\n ~ ~ ~ ~",
        "  .~~~.  \n ( - - ) \n  \\_^_/ \n /|/|\\|\\\n~ ~ ~ ~ ",
    ],
    "turtle": [
        "  _____  \n /({E} {E})\\\n| /_\\ |\n|_____|\n  U   U ",
        "  _____  \n /({E} {E})\\\n| /_\\ |\n|_____|\n U   U  ",
        "  _____  \n /(- -)\\\n| /_\\ |\n|_____|\n  U   U ",
    ],
    "fox": [
        " /\\ /\\  \n( {E} {E} ) \n  \\ w / \n  | | | \n _| |_  ",
        " /\\ /\\  \n( {E} {E} ) \n  \\ w / \n  || |  \n _| |_  ",
        " /\\ /\\  \n( - - ) \n  \\ w / \n  | | | \n _| |_  ",
    ],
    "wolf": [
        " /\\_/\\  \n({E}   {E}) \n  \\V/   \n  /|\\   \n_/ | \\_ ",
        " /\\_/\\  \n({E}   {E}) \n  \\V/   \n  \\|/   \n_/ | \\_ ",
        " /\\_/\\  \n(-   -) \n  \\V/   \n  /|\\   \n_/ | \\_ ",
    ],
    "phoenix": [
        " ~\\ /~  \n ({E} {E}) \n  \\|/   \n  /|\\   \n~/ | \\~ ",
        " ~/\\~   \n ({E} {E}) \n  \\|/   \n  /|\\   \n~/ | \\~ ",
        " ~\\ /~  \n (- -) \n  \\|/   \n  /|\\   \n~/ | \\~ ",
    ],
    "golem": [
        " [###]  \n [{E} {E}] \n [___]  \n /[ ]\\ \n_|   |_ ",
        " [###]  \n [{E} {E}] \n [___]  \n\\[ ] / \n_|   |_ ",
        " [###]  \n [_ _] \n [___]  \n /[ ]\\ \n_|   |_ ",
    ],
    "sprite": [
        "  *  *  \n * {E}{E} * \n  \\  /  \n   \\/   \n  ~  ~  ",
        " *  *   \n  *{E}{E}*  \n  \\  /  \n   \\/   \n ~  ~   ",
        "  *  *  \n * -- * \n  \\  /  \n   \\/   \n  ~  ~  ",
    ],
    "raven": [
        "  ___   \n ({E} {E})> \n /__\\  \n /  \\  \n_|  |_ ",
        "  ___   \n ({E} {E})> \n /__\\  \n  /\\   \n_|  |_ ",
        "  ___   \n (- -)> \n /__\\  \n /  \\  \n_|  |_ ",
    ],
    "serpent": [
        "  /{E}{E}\\  \n /    \\ \n(  ~~  )\n \\    / \n  ~~~~  ",
        "  /{E}{E}\\  \n /    \\ \n(  ~   )\n \\    / \n  ~~~~  ",
        "  /--\\  \n /    \\ \n(  ~~  )\n \\    / \n  ~~~~  ",
    ],
    "bear": [
        " (\\__/) \n ({E} {E}) \n  > < \n / | \\ \n_|   |_ ",
        " (\\__/) \n ({E} {E}) \n  > < \n  \\|/  \n_|   |_ ",
        " (\\__/) \n (- -) \n  > < \n / | \\ \n_|   |_ ",
    ],
    "hawk": [
        "  \\ /   \n ({E}V{E}) \n  /_\\  \n  / \\   \n_/   \\_ ",
        "  \\ /   \n ({E}V{E}) \n  /_\\  \n  \\ /   \n_/   \\_ ",
        "  \\ /   \n (-V-) \n  /_\\  \n  / \\   \n_/   \\_ ",
    ],
    "panther": [
        "  ___   \n ({E} {E}) \n  \\_/  \n  /|\\   \n_/ | \\_ ",
        "  ___   \n ({E} {E}) \n  \\_/  \n  \\|/   \n_/ | \\_ ",
        "  ___   \n (- -) \n  \\_/  \n  /|\\   \n_/ | \\_ ",
    ],
}


# ── Render functions ──

def render_sprite(bones: CompanionBones, frame: int = 0) -> str:
    """Render the companion sprite with eye substitution."""
    species = bones.species
    if species not in SPRITES:
        species = "dragon"  # fallback
    frames = SPRITES[species]
    sprite = frames[frame % len(frames)]
    return sprite.replace("{E}", bones.eye)

def render_face(bones: CompanionBones) -> str:
    """Compact one-line face for narrow terminals."""
    e = bones.eye
    faces = {
        "dragon": f"({e}>{e})",
        "cat": f"={e}^{e}=",
        "owl": f"({e}v{e})",
        "penguin": f"({e}_{e})",
        "ghost": f"~{e}o{e}~",
        "robot": f"[{e}_{e}]",
        "octopus": f"({e}~{e})",
        "turtle": f"({e}.{e})",
        "fox": f"({e}w{e})",
        "wolf": f"({e}V{e})",
        "phoenix": f"~{e}^{e}~",
        "golem": f"[{e}#{e}]",
        "sprite": f"*{e}*{e}*",
        "raven": f"({e}>{e})",
        "serpent": f"~{e}~{e}~",
        "bear": f"({e}<{e})",
        "hawk": f"({e}V{e})",
        "panther": f"({e}_{e})",
    }
    return faces.get(bones.species, f"({e}.{e})")


# ── Companion Definitions (static, original) ──

COMPANIONS = {
    "friday": {
        "name": "F.R.I.D.A.Y.",
        "type": "AI COMPANION",
        "rarity": "LEGENDARY",
        "art": [
            r"      ╔══╗      ",
            r"     ╔╝◈◈╚╗     ",
            r"    ╔╝ ≋≋ ╚╗    ",
            r"     ╚╗  ╔╝     ",
            r"      ╚══╝      ",
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
        "rarity": "RARE",
        "art": [
            r"     /█████\     ",
            r"    ║ ◯   ◯ ║    ",
            r"    ║  ═══  ║    ",
            r"     ╲█████╱     ",
            r"      ║   ║      ",
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
        "rarity": "COMMON",
        "art": [
            r"      ◆◆◆      ",
            r"     ◆ ∞ ◆     ",
            r"      ◆◆◆      ",
            r"       ║       ",
            r"      ═╩═      ",
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
        self.enabled = False  # companion must be explicitly activated
        self.last_comment_time = 0
        self.comment_cooldown = 30  # seconds between unsolicited comments

        # Buddy system attributes
        self.bones: CompanionBones | None = None
        self.soul: CompanionSoul | None = None
        self._frame: int = 0
        self._idle_sequence = [0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0]

    @property
    def name(self):
        return self.data["name"]

    def generate(self, user_id: str = ""):
        """Generate deterministic bones from user ID, set species/name."""
        bones, seed = generate_companion(user_id)
        self.bones = bones
        self.soul = CompanionSoul(
            name=bones.species.capitalize(),
            personality=bones.rarity,
            hatched_at=time.time(),
        )
        # Update the data dict so render_card and name stay consistent
        species_type_map = {
            "dragon": "FIRE WYRM", "cat": "SHADOW CAT", "owl": "NIGHT OWL",
            "penguin": "ICE SCOUT", "ghost": "PHANTOM", "robot": "MECH UNIT",
            "octopus": "DEEP LURKER", "turtle": "SHELL SAGE", "fox": "SLY TRICKSTER",
            "wolf": "PACK LEADER", "phoenix": "REBORN FLAME", "golem": "STONE WARD",
            "sprite": "PIXIE SPARK", "raven": "DARK HERALD", "serpent": "COIL VIPER",
            "bear": "IRON BEAR", "hawk": "SKY RAZOR", "panther": "VOID STALKER",
        }
        rarity_upper = bones.rarity.upper()
        type_label = species_type_map.get(bones.species, "COMPANION")
        # Build sprite art lines from frame 0
        sprite_text = render_sprite(bones, 0)
        art_lines = sprite_text.split("\n")

        self.data = {
            "name": self.soul.name,
            "type": type_label,
            "rarity": rarity_upper,
            "art": art_lines,
            "desc": (
                f'"A {bones.rarity} {bones.species} companion\n'
                f'born from seed #{seed:08x}.\n'
                f'Eyes: {bones.eye}  Hat: {bones.hat}\n'
                f'{"SHINY! Gleams in the terminal light." if bones.shiny else "Loyal and ever-watchful."}"'
            ),
            "stats": bones.stats,
        }

    def get_animated_sprite(self) -> str:
        """Return current animation frame, advance counter."""
        if self.bones is None:
            return ""
        idx = self._idle_sequence[self._frame % len(self._idle_sequence)]
        # idx: 0=idle, 1=sway, 2=blink, -1=idle (reverse treated as 0)
        frame_num = max(0, idx)
        self._frame += 1
        return render_sprite(self.bones, frame_num)

    def get_stat_card(self) -> str:
        """Render stats as a formatted card with rarity stars."""
        if self.bones is None:
            return "No companion generated yet. Use generate() first."
        bones = self.bones
        stars = RARITY_STARS.get(bones.rarity, "★")
        rarity_colors = {
            "common": DIM, "uncommon": GREEN, "rare": BLUE,
            "epic": MAGENTA, "legendary": YELLOW,
        }
        rc = rarity_colors.get(bones.rarity, DIM)

        lines = []
        lines.append(f"{rc}{stars} {bones.rarity.upper()}{RESET}  {BOLD}{bones.species.upper()}{RESET}")
        if bones.shiny:
            lines.append(f"  {YELLOW}~ SHINY ~{RESET}")
        lines.append(f"  Eyes: {bones.eye}  Hat: {bones.hat}")
        lines.append("")
        for stat_name, stat_val in bones.stats.items():
            filled = stat_val // 10
            empty = 10 - filled
            if stat_val >= 80:
                bar_color = GREEN
            elif stat_val >= 50:
                bar_color = YELLOW
            else:
                bar_color = RED
            bar = f"{bar_color}{'█' * filled}{DIM}{'░' * empty}{RESET}"
            lines.append(f"  {stat_name:<10s} {bar} {stat_val:>3d}")
        return "\n".join(lines)

    def render_card(self, tw: int = 80) -> str:
        """Render the companion card (shown on /buddy). Matches JARVIS style."""
        d = self.data
        W = 38
        rarity = d.get("rarity", "COMMON")

        # Rarity color
        rarity_colors = {
            "COMMON": DIM,
            "UNCOMMON": GREEN,
            "RARE": BLUE,
            "EPIC": MAGENTA,
            "LEGENDARY": YELLOW,
        }
        rc = rarity_colors.get(rarity, DIM)

        lines = []
        lines.append(f"╭{'─' * W}╮")
        lines.append(f"│{' ' * W}│")
        # Rarity + Type header
        left = f"  ★ {rarity}"
        right = f"{d['type']}  "
        gap = W - len(left) - len(right)
        lines.append(f"│{rc}{left}{' ' * gap}{right}{RESET}│")
        lines.append(f"│{' ' * W}│")
        lines.append(f"│{' ' * W}│")
        # Art (centered)
        for art_line in d["art"]:
            visible = len(art_line)
            pad_l = (W - visible) // 2
            pad_r = W - pad_l - visible
            lines.append(f"│{' ' * pad_l}{art_line}{' ' * pad_r}│")
        lines.append(f"│{' ' * W}│")
        # Name
        name_text = d['name']
        lines.append(f"│  {BOLD}{name_text}{RESET}{' ' * (W - 2 - len(name_text))}│")
        lines.append(f"│{' ' * W}│")
        # Description
        for desc_line in d["desc"].strip('"').split("\n"):
            quoted = f'"{desc_line}"'
            visible = len(quoted)
            lines.append(f"│  {DIM}{quoted}{RESET}{' ' * (W - 2 - visible)}│")
        lines.append(f"│{' ' * W}│")
        # Stats with colored bars
        for stat_name, stat_val in d["stats"].items():
            filled = stat_val // 10
            empty = 10 - filled
            # Color bar based on value
            if stat_val >= 80:
                bar_color = GREEN
            elif stat_val >= 50:
                bar_color = YELLOW
            else:
                bar_color = RED
            bar = f"{bar_color}{'█' * filled}{DIM}{'░' * empty}{RESET}"
            label = f"  {stat_name:<10s} "
            num = f"  {stat_val:>3d}"
            # Calculate padding (bar has ANSI codes so use visible length)
            visible_len = 2 + 10 + 1 + 10 + 2 + 3 + 5  # label + bar + num + padding
            pad = W - visible_len
            lines.append(f"│{label}{bar}{num}{' ' * max(0, pad)}│")
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
        """Render a speech bubble with the companion's comment, with inline face."""
        name = self.data["name"].split(".")[0] if "." in self.data["name"] else self.data["name"]
        face = ""
        if self.bones is not None:
            face = render_face(self.bones) + " "
        return f"  {DIM}{face}{name}: {comment}{RESET}"

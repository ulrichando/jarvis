"""Companion buddy type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Species names
duck = "duck"
goose = "goose"
blob = "blob"
cat = "cat"
dragon = "dragon"
octopus = "octopus"
owl = "owl"
penguin = "penguin"
turtle = "turtle"
snail = "snail"
ghost = "ghost"
axolotl = "axolotl"
capybara = "capybara"
cactus = "cactus"
robot = "robot"
rabbit = "rabbit"
mushroom = "mushroom"
chonk = "chonk"

SPECIES = [
    duck, goose, blob, cat, dragon, octopus, owl, penguin,
    turtle, snail, ghost, axolotl, capybara, cactus, robot,
    rabbit, mushroom, chonk,
]
Species = str

RARITIES = ["common", "uncommon", "rare", "epic", "legendary"]
Rarity = Literal["common", "uncommon", "rare", "epic", "legendary"]

EYES = ["·", "✦", "×", "◉", "@", "°"]
Eye = str

HATS = ["none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck"]
Hat = str

STAT_NAMES = ["DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK"]
StatName = Literal["DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK"]


@dataclass
class CompanionBones:
    """Deterministic parts -- derived from hash(userId)."""
    rarity: Rarity
    species: Species
    eye: Eye
    hat: Hat
    shiny: bool
    stats: dict[StatName, int]


@dataclass
class CompanionSoul:
    """Model-generated soul -- stored in config after first hatch."""
    name: str
    personality: str


@dataclass
class Companion:
    """Full companion: bones + soul."""
    rarity: Rarity
    species: Species
    eye: Eye
    hat: Hat
    shiny: bool
    stats: dict[StatName, int]
    name: str
    personality: str
    hatched_at: int = 0


@dataclass
class StoredCompanion:
    """What persists in config. Bones regenerated from hash(userId)."""
    name: str
    personality: str
    hatched_at: int = 0


RARITY_WEIGHTS: dict[Rarity, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}

RARITY_STARS: dict[Rarity, str] = {
    "common": "★",
    "uncommon": "★★",
    "rare": "★★★",
    "epic": "★★★★",
    "legendary": "★★★★★",
}

RARITY_COLORS: dict[Rarity, str] = {
    "common": "inactive",
    "uncommon": "success",
    "rare": "permission",
    "epic": "autoAccept",
    "legendary": "warning",
}

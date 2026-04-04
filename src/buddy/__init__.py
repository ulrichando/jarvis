# Buddy companion system
from .types import (
    SPECIES as BUDDY_SPECIES, RARITIES as BUDDY_RARITIES,
    RARITY_WEIGHTS as BUDDY_RARITY_WEIGHTS, RARITY_STARS as BUDDY_RARITY_STARS,
    RARITY_COLORS, EYES as BUDDY_EYES, HATS as BUDDY_HATS,
    STAT_NAMES as BUDDY_STAT_NAMES,
    CompanionBones as BuddyBones, CompanionSoul as BuddySoul,
    Companion as BuddyCompanion, StoredCompanion,
)
from .sprites import render_sprite as buddy_render_sprite, render_face as buddy_render_face
from .companion import roll, roll_with_seed, get_companion
from .prompt import companion_intro_text, get_companion_intro_attachment

__all__ = [
    "BUDDY_SPECIES", "BUDDY_RARITIES", "BUDDY_RARITY_WEIGHTS", "BUDDY_RARITY_STARS",
    "RARITY_COLORS", "BUDDY_EYES", "BUDDY_HATS", "BUDDY_STAT_NAMES",
    "BuddyBones", "BuddySoul", "BuddyCompanion", "StoredCompanion",
    "buddy_render_sprite", "buddy_render_face",
    "roll", "roll_with_seed", "get_companion",
    "companion_intro_text", "get_companion_intro_attachment",
]

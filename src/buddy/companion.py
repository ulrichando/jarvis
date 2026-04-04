"""Companion generation from user ID via deterministic PRNG."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, TypeVar

from .types import (
    EYES, HATS, RARITIES, RARITY_WEIGHTS, SPECIES, STAT_NAMES,
    Companion, CompanionBones, Rarity, StatName,
)

T = TypeVar("T")

SALT = "friend-2026-401"


def _mulberry32(seed: int):
    """Mulberry32 -- tiny seeded PRNG."""
    a = seed & 0xFFFFFFFF

    def _next() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (1 | a)) & 0xFFFFFFFF
        t = ((t + ((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return _next


def _hash_string(s: str) -> int:
    """FNV-1a hash to 32-bit unsigned."""
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _pick(rng, arr: list):
    return arr[int(math.floor(rng() * len(arr)))]


def _roll_rarity(rng) -> Rarity:
    total = sum(RARITY_WEIGHTS.values())
    roll = rng() * total
    for rarity in RARITIES:
        roll -= RARITY_WEIGHTS[rarity]
        if roll < 0:
            return rarity
    return "common"


RARITY_FLOOR: dict[Rarity, int] = {
    "common": 5,
    "uncommon": 15,
    "rare": 25,
    "epic": 35,
    "legendary": 50,
}


def _roll_stats(rng, rarity: Rarity) -> dict[StatName, int]:
    """One peak stat, one dump stat, rest scattered. Rarity bumps the floor."""
    floor = RARITY_FLOOR[rarity]
    peak = _pick(rng, list(STAT_NAMES))
    dump = _pick(rng, list(STAT_NAMES))
    while dump == peak:
        dump = _pick(rng, list(STAT_NAMES))

    stats: dict[StatName, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(math.floor(rng() * 30)))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(math.floor(rng() * 15)))
        else:
            stats[name] = floor + int(math.floor(rng() * 40))
    return stats


@dataclass
class Roll:
    bones: CompanionBones
    inspiration_seed: int


def _roll_from(rng) -> Roll:
    rarity = _roll_rarity(rng)
    bones = CompanionBones(
        rarity=rarity,
        species=_pick(rng, SPECIES),
        eye=_pick(rng, EYES),
        hat="none" if rarity == "common" else _pick(rng, HATS),
        shiny=rng() < 0.01,
        stats=_roll_stats(rng, rarity),
    )
    return Roll(bones=bones, inspiration_seed=int(math.floor(rng() * 1e9)))


_roll_cache: Optional[tuple[str, Roll]] = None


def roll(user_id: str) -> Roll:
    """Get deterministic roll for a user ID (cached)."""
    global _roll_cache
    key = user_id + SALT
    if _roll_cache and _roll_cache[0] == key:
        return _roll_cache[1]
    value = _roll_from(_mulberry32(_hash_string(key)))
    _roll_cache = (key, value)
    return value


def roll_with_seed(seed: str) -> Roll:
    return _roll_from(_mulberry32(_hash_string(seed)))


def companion_user_id() -> str:
    """Get user ID for companion generation."""
    # Simplified -- in real usage would read from global config
    return "anon"


def get_companion() -> Optional[Companion]:
    """Regenerate bones from userId, merge with stored soul."""
    # Simplified -- would read from global config
    return None

"""
Random word slug generator for plan IDs.
Inspired by https://github.com/nas5w/random-word-slugs
"""

import os
import secrets
from typing import List, Tuple

ADJECTIVES: Tuple[str, ...] = (
    "abundant", "ancient", "bright", "calm", "cheerful", "clever", "cozy",
    "curious", "dapper", "dazzling", "deep", "delightful", "eager", "elegant",
    "enchanted", "fancy", "fluffy", "gentle", "gleaming", "golden", "graceful",
    "happy", "hidden", "humble", "jolly", "joyful", "keen", "kind", "lively",
    "lovely", "lucky", "luminous", "magical", "majestic", "mellow", "merry",
    "mighty", "misty", "noble", "peaceful", "playful", "polished", "precious",
    "proud", "quiet", "quirky", "radiant", "rosy", "serene", "shiny", "silly",
    "sleepy", "smooth", "snazzy", "snug", "snuggly", "soft", "sparkling",
    "spicy", "splendid", "sprightly", "starry", "steady", "sunny", "swift",
    "tender", "tidy", "toasty", "tranquil", "twinkly", "valiant", "vast",
    "velvet", "vivid", "warm", "whimsical", "wild", "wise", "witty",
    "wondrous", "zany", "zesty", "zippy", "breezy", "bubbly", "buzzing",
    "cheeky", "cosmic", "crispy", "crystalline", "cuddly", "drifting",
    "dreamy", "effervescent", "ethereal", "fizzy", "flickering", "floating",
    "fluttering", "foamy", "frolicking", "fuzzy", "giggly", "glimmering",
    "glistening", "glittery", "glowing", "goofy", "groovy", "harmonic",
    "hazy", "humming", "iridescent", "jaunty", "jazzy", "jiggly", "melodic",
    "moonlit", "mossy", "nifty", "peppy", "prancy", "purrfect", "purring",
    "quizzical", "rippling", "rustling", "shimmering", "shimmying", "snappy",
    "squishy", "swirling", "ticklish", "tingly", "twinkling", "velvety",
    "wiggly", "wobbly", "woolly",
)

NOUNS: Tuple[str, ...] = (
    "aurora", "avalanche", "blossom", "breeze", "brook", "bubble", "canyon",
    "cascade", "cloud", "clover", "comet", "coral", "cosmos", "creek",
    "crescent", "crystal", "dawn", "dewdrop", "dusk", "eclipse", "ember",
    "feather", "fern", "firefly", "flame", "flurry", "fog", "forest",
    "frost", "galaxy", "garden", "glacier", "glade", "grove", "harbor",
    "horizon", "island", "lagoon", "lake", "leaf", "lightning", "meadow",
    "meteor", "mist", "moon", "moonbeam", "mountain", "nebula", "nova",
    "ocean", "orbit", "pebble", "petal", "pine", "planet", "pond", "puddle",
    "quasar", "rain", "rainbow", "reef", "ripple", "river", "shore", "sky",
    "snowflake", "spark", "spring", "star", "stardust", "starlight", "storm",
    "stream", "summit", "sun", "sunbeam", "sunrise", "sunset", "thunder",
    "tide", "twilight", "valley", "volcano", "waterfall", "wave", "willow",
    "wind", "alpaca", "axolotl", "badger", "bear", "beaver", "bee", "bird",
    "bumblebee", "bunny", "cat", "chipmunk", "crab", "crane", "deer",
    "dolphin", "dove", "dragon", "dragonfly", "duckling", "eagle", "elephant",
    "falcon", "finch", "flamingo", "fox", "frog", "giraffe", "goose",
    "hamster", "hare", "hedgehog", "hippo", "hummingbird", "jellyfish",
    "kitten", "koala", "ladybug", "lark", "lemur", "llama", "lobster",
    "lynx", "manatee", "meerkat", "moth", "narwhal", "newt", "octopus",
    "otter", "owl", "panda", "parrot", "peacock", "pelican", "penguin",
    "phoenix", "piglet", "platypus", "pony", "porcupine", "puffin", "puppy",
    "quail", "quokka", "rabbit", "raccoon", "raven", "robin", "salamander",
    "seahorse", "seal", "sloth", "snail", "sparrow", "sphinx", "squid",
    "squirrel", "starfish", "swan", "tiger", "toucan", "turtle", "unicorn",
    "walrus", "whale", "wolf", "wombat", "wren", "yeti", "zebra",
)

VERBS: Tuple[str, ...] = (
    "baking", "beaming", "booping", "bouncing", "brewing", "bubbling",
    "chasing", "churning", "coalescing", "conjuring", "cooking", "crafting",
    "crunching", "cuddling", "dancing", "dazzling", "discovering", "doodling",
    "dreaming", "drifting", "enchanting", "exploring", "finding", "floating",
    "fluttering", "foraging", "forging", "frolicking", "gathering", "giggling",
    "gliding", "greeting", "growing", "hatching", "herding", "honking",
    "hopping", "hugging", "humming", "imagining", "inventing", "jingling",
    "juggling", "jumping", "kindling", "knitting", "launching", "leaping",
    "mapping", "marinating", "meandering", "mixing", "moseying", "munching",
    "napping", "nibbling", "noodling", "orbiting", "painting", "percolating",
    "petting", "plotting", "pondering", "popping", "prancing", "purring",
    "puzzling", "questing", "riding", "roaming", "rolling", "scribbling",
    "seeking", "shimmying", "singing", "skipping", "sleeping", "snacking",
    "sniffing", "snuggling", "soaring", "sparking", "spinning", "splashing",
    "sprouting", "squishing", "stargazing", "stirring", "strolling",
    "swimming", "swinging", "tickling", "tinkering", "toasting", "tumbling",
    "twirling", "waddling", "wandering", "watching", "weaving", "whistling",
    "wiggling", "wishing", "wobbling", "wondering", "yawning", "zooming",
)


def _pick_random(array: Tuple[str, ...]) -> str:
    """Pick a cryptographically random element from a tuple."""
    return secrets.choice(array)


def generate_word_slug() -> str:
    """
    Generate a random word slug in the format "adjective-verb-noun".
    Example: "gleaming-brewing-phoenix", "cosmic-pondering-lighthouse"
    """
    adjective = _pick_random(ADJECTIVES)
    verb = _pick_random(VERBS)
    noun = _pick_random(NOUNS)
    return f"{adjective}-{verb}-{noun}"


def generate_short_word_slug() -> str:
    """
    Generate a shorter random word slug in the format "adjective-noun".
    Example: "graceful-unicorn", "cosmic-lighthouse"
    """
    adjective = _pick_random(ADJECTIVES)
    noun = _pick_random(NOUNS)
    return f"{adjective}-{noun}"

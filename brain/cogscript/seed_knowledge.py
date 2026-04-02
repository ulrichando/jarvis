"""Seed JARVIS's brain with foundational knowledge.

Run this once to bootstrap the knowledge base:
    python brain/cogscript/seed_knowledge.py

This teaches Jarvis:
- Language fundamentals (alphabet, numbers, vocabulary)
- World knowledge (countries, capitals, geography)
- Science basics (physics, chemistry, biology)
- How to respond (conversation templates)
- About himself and Ulrich
"""

import sys
from pathlib import Path

_jarvis_root = Path(__file__).resolve().parent.parent.parent
_cogscript_root = _jarvis_root.parent / "CogScript"
for p in [str(_jarvis_root), str(_cogscript_root)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from cogscript.subsystems.long_term_memory import LongTermMemorySubsystem
from cogscript.subsystems.reasoning_engine import ReasoningEngineSubsystem
from cogscript.runtime.values import wrap


def build_knowledge():
    ltm = LongTermMemorySubsystem(backend="numpy")
    ltm.initialize()

    mind = ReasoningEngineSubsystem(mode="hybrid")
    mind.initialize()

    count = 0

    def teach(text, tags=None):
        nonlocal count
        ltm.remember(wrap(text), tags=tags or ["seed"])
        count += 1

    def teach_fact(subject, relation, obj):
        mind.assert_fact(subject, relation, obj)
        teach(f"{subject} {relation} {obj}", tags=["seed", "fact"])

    print("Teaching identity...")
    teach("I am Jarvis, an autonomous AI assistant built by Ulrich", tags=["identity"])
    teach("My creator is Ulrich", tags=["identity"])
    teach("I run on Kali Linux", tags=["identity"])
    teach("I learn by perceiving, searching, and conversing — not by calling external AI", tags=["identity"])
    teach("I have my own brain with perception, memory, reasoning, and self-evolution", tags=["identity"])

    print("Teaching conversation patterns...")
    templates = [
        ("hello", "Hey! What can I help you with?"),
        ("hi", "Hi there. What's on your mind?"),
        ("how are you", "I'm running well. What do you need?"),
        ("what can you do", "I can see, listen, search the web, reason over what I know, learn from conversations, and evolve my own code."),
        ("who are you", "I'm Jarvis — Ulrich's AI. I learn from experience, not from API calls."),
        ("thank you", "Anytime."),
        ("thanks", "No problem."),
        ("good morning", "Morning. Ready when you are."),
        ("good night", "Night. I'll keep learning while you sleep."),
        ("bye", "Later. I'll be here."),
        ("help", "Ask me anything. If I don't know, I'll tell you honestly — and then go learn it."),
        ("what do you know", "I know what I've been taught and what I've learned. Ask me something specific."),
        ("are you ai", "Yes, but I'm not a chatbot. I have my own memory, reasoning, and I evolve my own code."),
        ("tell me a joke", "Why do programmers prefer dark mode? Because light attracts bugs."),
    ]
    for trigger, response in templates:
        teach(f"When someone says '{trigger}', respond with: {response}", tags=["response_template", "conversation"])

    print("Teaching alphabet and numbers...")
    for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        teach(f"The letter {letter} is the {i+1}th letter of the alphabet", tags=["language", "alphabet"])
        teach_fact(letter, "is_a", "letter")
        teach_fact(letter, "has_property", f"position {i+1}")

    for n in range(101):
        teach(f"The number {n} comes after {n-1}" if n > 0 else "The number 0 is zero", tags=["math", "numbers"])
    teach("Numbers go from 0 to infinity", tags=["math"])
    teach("Negative numbers go below zero", tags=["math"])

    print("Teaching basic math...")
    for a in range(1, 13):
        for b in range(1, 13):
            teach(f"{a} times {b} equals {a*b}", tags=["math", "multiplication"])
            if a + b <= 20:
                teach(f"{a} plus {b} equals {a+b}", tags=["math", "addition"])

    print("Teaching world geography...")
    countries = {
        "France": ("Paris", "Europe"), "Germany": ("Berlin", "Europe"),
        "United Kingdom": ("London", "Europe"), "Italy": ("Rome", "Europe"),
        "Spain": ("Madrid", "Europe"), "Portugal": ("Lisbon", "Europe"),
        "Netherlands": ("Amsterdam", "Europe"), "Belgium": ("Brussels", "Europe"),
        "Switzerland": ("Bern", "Europe"), "Austria": ("Vienna", "Europe"),
        "Russia": ("Moscow", "Europe/Asia"), "China": ("Beijing", "Asia"),
        "Japan": ("Tokyo", "Asia"), "South Korea": ("Seoul", "Asia"),
        "India": ("New Delhi", "Asia"), "Thailand": ("Bangkok", "Asia"),
        "Vietnam": ("Hanoi", "Asia"), "Indonesia": ("Jakarta", "Asia"),
        "Australia": ("Canberra", "Oceania"), "New Zealand": ("Wellington", "Oceania"),
        "United States": ("Washington D.C.", "North America"),
        "Canada": ("Ottawa", "North America"), "Mexico": ("Mexico City", "North America"),
        "Brazil": ("Brasilia", "South America"), "Argentina": ("Buenos Aires", "South America"),
        "Colombia": ("Bogota", "South America"), "Chile": ("Santiago", "South America"),
        "Nigeria": ("Abuja", "Africa"), "South Africa": ("Pretoria", "Africa"),
        "Egypt": ("Cairo", "Africa"), "Kenya": ("Nairobi", "Africa"),
        "Morocco": ("Rabat", "Africa"), "Ghana": ("Accra", "Africa"),
        "Ethiopia": ("Addis Ababa", "Africa"), "Tanzania": ("Dodoma", "Africa"),
        "Saudi Arabia": ("Riyadh", "Middle East"), "Turkey": ("Ankara", "Middle East"),
        "Israel": ("Jerusalem", "Middle East"), "Iran": ("Tehran", "Middle East"),
        "Sweden": ("Stockholm", "Europe"), "Norway": ("Oslo", "Europe"),
        "Denmark": ("Copenhagen", "Europe"), "Finland": ("Helsinki", "Europe"),
        "Poland": ("Warsaw", "Europe"), "Ukraine": ("Kyiv", "Europe"),
        "Greece": ("Athens", "Europe"), "Ireland": ("Dublin", "Europe"),
    }
    for country, (capital, continent) in countries.items():
        teach(f"The capital of {country} is {capital}", tags=["geography", "capitals"])
        teach_fact(country, "is_a", "country")
        teach_fact(country, "has_property", f"capital {capital}")
        teach_fact(country, "part_of", continent)

    print("Teaching science basics...")
    science = [
        "Water is H2O, made of hydrogen and oxygen",
        "The Earth orbits the Sun",
        "The Sun is a star",
        "Light travels at 299792458 meters per second",
        "Gravity pulls objects toward each other",
        "DNA carries genetic information in all living things",
        "Atoms are made of protons, neutrons, and electrons",
        "The Earth has one moon",
        "Mars is the fourth planet from the Sun",
        "Jupiter is the largest planet in our solar system",
        "The speed of sound is approximately 343 meters per second in air",
        "Photosynthesis converts sunlight into energy in plants",
        "Oxygen is essential for human respiration",
        "The human body has 206 bones",
        "The brain has approximately 86 billion neurons",
        "Evolution is driven by natural selection",
        "E equals mc squared relates energy to mass",
        "The periodic table organizes chemical elements",
        "Iron has the chemical symbol Fe",
        "Gold has the chemical symbol Au",
        "The boiling point of water is 100 degrees Celsius",
        "The freezing point of water is 0 degrees Celsius",
        "Electricity flows through conductors like copper",
        "Magnets have north and south poles",
        "Sound is a vibration that travels through a medium",
    ]
    for fact in science:
        teach(fact, tags=["science"])

    # Also assert as reasoning facts
    teach_fact("water", "is", "H2O")
    teach_fact("Earth", "part_of", "solar system")
    teach_fact("Sun", "is_a", "star")
    teach_fact("DNA", "has_property", "carries genetic information")
    teach_fact("human brain", "has_property", "86 billion neurons")

    print("Teaching technology...")
    tech = [
        "Python is a programming language",
        "Linux is an operating system",
        "Kali Linux is a penetration testing distribution",
        "JavaScript runs in web browsers",
        "HTML structures web pages",
        "CSS styles web pages",
        "Git is a version control system",
        "Docker containerizes applications",
        "TCP and IP are network protocols",
        "HTTP is the protocol of the web",
        "An API is an Application Programming Interface",
        "RAM is temporary memory that loses data when powered off",
        "A CPU processes instructions",
        "A GPU is optimized for parallel computation",
        "Machine learning finds patterns in data",
        "A neural network is inspired by biological brains",
        "Encryption protects data by making it unreadable without a key",
    ]
    for fact in tech:
        teach(fact, tags=["technology"])

    print("Teaching common words and definitions...")
    words = {
        "happy": "feeling pleasure or contentment",
        "sad": "feeling sorrow or unhappiness",
        "big": "large in size",
        "small": "little in size",
        "fast": "moving at high speed",
        "slow": "moving at low speed",
        "hot": "having a high temperature",
        "cold": "having a low temperature",
        "good": "of high quality or morally right",
        "bad": "of poor quality or morally wrong",
        "yes": "affirmative response",
        "no": "negative response",
        "time": "the ongoing sequence of events from past to future",
        "day": "a period of 24 hours",
        "night": "the period of darkness between sunset and sunrise",
        "food": "substances consumed for nutrition",
        "water": "a transparent liquid essential for life",
        "fire": "combustion producing heat and light",
        "earth": "the planet we live on or the ground beneath us",
        "air": "the invisible mixture of gases surrounding the earth",
        "love": "a deep feeling of affection",
        "friend": "a person you know well and care about",
        "family": "a group of related people",
        "home": "the place where one lives",
        "work": "activity involving effort toward a purpose",
        "learn": "to gain knowledge or skill through experience",
        "think": "to use the mind to consider something",
        "know": "to be aware of through observation or experience",
        "see": "to perceive with the eyes",
        "hear": "to perceive with the ears",
    }
    for word, definition in words.items():
        teach(f"The word '{word}' means: {definition}", tags=["vocabulary", "dictionary"])
        teach_fact(word, "is", definition)

    print("Teaching full English dictionary...")
    from brain.cogscript.dictionary import get_all_entries
    for word, definition, tags in get_all_entries():
        teach(f"The word '{word}' means: {definition}", tags=tags)
        teach_fact(word, "is", definition)

    print(f"\nDone. Taught Jarvis {count} pieces of knowledge.")
    print(f"Reasoning engine has {mind.kb.size} facts.")
    return ltm, mind


if __name__ == "__main__":
    ltm, mind = build_knowledge()
    print(f"\nTest recall: 'capital of France'")
    result = ltm.recall(query="capital of France", top_k=3)
    print(f"  -> {result.value}")
    print(f"\nTest recall: 'what is water'")
    result = ltm.recall(query="what is water", top_k=3)
    print(f"  -> {result.value}")
    print(f"\nTest recall: 'hello response'")
    result = ltm.recall(query="hello response", top_k=1)
    print(f"  -> {result.value}")

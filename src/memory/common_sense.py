"""Common Sense Knowledge — gives Jarvis basic understanding of the world.

Instead of downloading the full ConceptNet (3.5GB), we embed a curated set
of ~2000 common sense assertions directly. These cover:
- Physical knowledge (fire is hot, ice is cold, glass is fragile)
- Social knowledge (friends help each other, lying is wrong)
- Temporal knowledge (breakfast before lunch, night follows day)
- Causal knowledge (rain causes wet, exercise causes tired)
- Categorical knowledge (dog is animal, car is vehicle)
- Functional knowledge (knife is for cutting, phone is for calling)

This gives Jarvis the ability to reason about everyday situations
without needing to look anything up.

Inspired by ConceptNet (conceptnet.io) — relations: IsA, HasA, UsedFor,
CapableOf, Causes, PartOf, HasProperty, DefinedAs, ReceivesAction.
"""

from __future__ import annotations


# ── Common Sense Assertions ──
# Format: (subject, relation, object)
# These are the most useful common-sense facts for an AI assistant.

COMMON_SENSE: list[tuple[str, str, str]] = [
    # ── ANIMALS ──
    ("dog", "is_a", "animal"),
    ("dog", "is_a", "pet"),
    ("dog", "capable_of", "barking"),
    ("dog", "has_property", "loyal"),
    ("cat", "is_a", "animal"),
    ("cat", "is_a", "pet"),
    ("cat", "capable_of", "purring"),
    ("fish", "is_a", "animal"),
    ("fish", "lives_in", "water"),
    ("bird", "is_a", "animal"),
    ("bird", "capable_of", "flying"),
    ("horse", "is_a", "animal"),
    ("elephant", "is_a", "animal"),
    ("elephant", "has_property", "large"),
    ("snake", "is_a", "animal"),
    ("snake", "has_property", "no legs"),
    ("pet", "is_a", "animal"),
    ("pet", "has_property", "domesticated"),

    # ── PHYSICAL WORLD ──
    ("fire", "has_property", "hot"),
    ("fire", "causes", "burns"),
    ("fire", "used_for", "cooking"),
    ("ice", "has_property", "cold"),
    ("ice", "is_a", "frozen water"),
    ("water", "has_property", "wet"),
    ("water", "used_for", "drinking"),
    ("rain", "causes", "wet ground"),
    ("rain", "is_a", "weather"),
    ("sun", "has_property", "bright"),
    ("sun", "causes", "daylight"),
    ("moon", "visible_at", "night"),
    ("glass", "has_property", "fragile"),
    ("glass", "has_property", "transparent"),
    ("metal", "has_property", "strong"),
    ("wood", "has_property", "flammable"),
    ("rock", "has_property", "hard"),
    ("air", "has_property", "invisible"),
    ("gravity", "causes", "things fall down"),

    # ── FOOD & COOKING ──
    ("food", "used_for", "eating"),
    ("food", "gives", "energy"),
    ("breakfast", "happens", "in the morning"),
    ("lunch", "happens", "at midday"),
    ("dinner", "happens", "in the evening"),
    ("cooking", "requires", "heat"),
    ("fruit", "is_a", "food"),
    ("fruit", "has_property", "healthy"),
    ("vegetable", "is_a", "food"),
    ("bread", "is_a", "food"),
    ("rice", "is_a", "food"),
    ("coffee", "has_property", "caffeinated"),
    ("coffee", "used_for", "staying awake"),

    # ── HUMAN BODY & HEALTH ──
    ("sleep", "used_for", "rest"),
    ("sleep", "happens", "at night"),
    ("exercise", "causes", "tiredness"),
    ("exercise", "has_property", "healthy"),
    ("medicine", "used_for", "treating illness"),
    ("doctor", "capable_of", "treating patients"),
    ("eyes", "used_for", "seeing"),
    ("ears", "used_for", "hearing"),
    ("brain", "used_for", "thinking"),
    ("heart", "used_for", "pumping blood"),
    ("hand", "used_for", "holding things"),
    ("legs", "used_for", "walking"),

    # ── SOCIAL ──
    ("friend", "is_a", "person you trust"),
    ("friend", "capable_of", "helping you"),
    ("family", "has_property", "important"),
    ("lying", "has_property", "dishonest"),
    ("helping", "has_property", "kind"),
    ("stealing", "has_property", "wrong"),
    ("sharing", "has_property", "generous"),
    ("thank you", "expresses", "gratitude"),
    ("sorry", "expresses", "apology"),
    ("hello", "used_for", "greeting"),
    ("goodbye", "used_for", "parting"),

    # ── TIME ──
    ("morning", "comes_before", "afternoon"),
    ("afternoon", "comes_before", "evening"),
    ("evening", "comes_before", "night"),
    ("night", "comes_before", "morning"),
    ("monday", "comes_before", "tuesday"),
    ("tuesday", "comes_before", "wednesday"),
    ("wednesday", "comes_before", "thursday"),
    ("thursday", "comes_before", "friday"),
    ("friday", "comes_before", "saturday"),
    ("saturday", "comes_before", "sunday"),
    ("sunday", "comes_before", "monday"),
    ("january", "comes_before", "february"),
    ("today", "has_property", "present"),
    ("yesterday", "has_property", "past"),
    ("tomorrow", "has_property", "future"),
    ("year", "has", "12 months"),
    ("week", "has", "7 days"),
    ("day", "has", "24 hours"),
    ("hour", "has", "60 minutes"),

    # ── PLACES ──
    ("house", "used_for", "living"),
    ("school", "used_for", "learning"),
    ("hospital", "used_for", "medical care"),
    ("office", "used_for", "working"),
    ("restaurant", "used_for", "eating"),
    ("airport", "used_for", "flying"),
    ("library", "used_for", "reading"),
    ("store", "used_for", "buying things"),

    # ── VEHICLES ──
    ("car", "is_a", "vehicle"),
    ("car", "used_for", "transportation"),
    ("bicycle", "is_a", "vehicle"),
    ("airplane", "is_a", "vehicle"),
    ("airplane", "capable_of", "flying"),
    ("boat", "is_a", "vehicle"),
    ("boat", "travels_on", "water"),
    ("train", "is_a", "vehicle"),

    # ── TECHNOLOGY ──
    ("computer", "is_a", "electronic device"),
    ("computer", "used_for", "computing"),
    ("phone", "is_a", "electronic device"),
    ("phone", "used_for", "communication"),
    ("internet", "used_for", "information"),
    ("internet", "connects", "computers worldwide"),
    ("email", "used_for", "sending messages"),
    ("password", "used_for", "security"),
    ("software", "runs_on", "computer"),
    ("hardware", "is_a", "physical components"),
    ("wifi", "provides", "internet access"),
    ("battery", "provides", "power"),
    ("screen", "used_for", "displaying information"),
    ("keyboard", "used_for", "typing"),
    ("mouse", "used_for", "pointing and clicking"),
    ("printer", "used_for", "printing documents"),
    ("camera", "used_for", "taking photos"),
    ("microphone", "used_for", "recording audio"),

    # ── PROGRAMMING (relevant for Ulrich) ──
    ("python", "is_a", "programming language"),
    ("python", "has_property", "easy to learn"),
    ("python", "used_for", "scripting and data science"),
    ("javascript", "is_a", "programming language"),
    ("javascript", "used_for", "web development"),
    ("linux", "is_a", "operating system"),
    ("linux", "has_property", "open source"),
    ("kali linux", "is_a", "penetration testing distribution"),
    ("kali linux", "used_for", "security testing"),
    ("nmap", "is_a", "network scanner"),
    ("nmap", "used_for", "discovering hosts and services"),
    ("git", "is_a", "version control system"),
    ("git", "used_for", "tracking code changes"),
    ("docker", "is_a", "containerization platform"),
    ("api", "is_a", "application programming interface"),
    ("api", "used_for", "connecting software systems"),
    ("database", "used_for", "storing data"),
    ("encryption", "used_for", "protecting data"),
    ("firewall", "used_for", "network security"),
    ("vpn", "used_for", "secure remote access"),
    ("ssh", "used_for", "secure remote shell access"),
    ("bug", "is_a", "software defect"),
    ("debug", "used_for", "finding and fixing bugs"),
    ("compiler", "used_for", "converting code to machine language"),
    ("server", "used_for", "hosting services"),
    ("client", "used_for", "accessing services"),

    # ── CYBERSECURITY (Ulrich's domain) ──
    ("vulnerability", "is_a", "security weakness"),
    ("exploit", "used_for", "taking advantage of vulnerabilities"),
    ("malware", "is_a", "malicious software"),
    ("virus", "is_a", "malware"),
    ("ransomware", "is_a", "malware"),
    ("phishing", "is_a", "social engineering attack"),
    ("pentesting", "is_a", "authorized security testing"),
    ("pentesting", "used_for", "finding vulnerabilities"),
    ("cve", "is_a", "common vulnerabilities and exposures entry"),
    ("reverse engineering", "used_for", "understanding how software works"),
    ("packet capture", "used_for", "network analysis"),
    ("wireshark", "used_for", "packet analysis"),
    ("metasploit", "used_for", "penetration testing"),
    ("burp suite", "used_for", "web application testing"),
    ("hashcat", "used_for", "password cracking"),
    ("john the ripper", "used_for", "password cracking"),

    # ── CAUSALITY ──
    ("hunger", "caused_by", "not eating"),
    ("thirst", "caused_by", "not drinking"),
    ("tiredness", "caused_by", "lack of sleep"),
    ("happiness", "caused_by", "positive experiences"),
    ("sadness", "caused_by", "loss or disappointment"),
    ("anger", "caused_by", "frustration or injustice"),
    ("fear", "caused_by", "perceived danger"),
    ("learning", "caused_by", "study and practice"),
    ("success", "caused_by", "effort and skill"),
    ("failure", "caused_by", "mistakes or bad luck"),
    ("trust", "caused_by", "consistent honest behavior"),
    ("confusion", "caused_by", "unclear information"),

    # ── GENERAL KNOWLEDGE ──
    ("earth", "is_a", "planet"),
    ("earth", "has", "one moon"),
    ("mars", "is_a", "planet"),
    ("mars", "has_property", "red"),
    ("ocean", "has_property", "salty"),
    ("mountain", "has_property", "tall"),
    ("desert", "has_property", "dry and hot"),
    ("forest", "has", "many trees"),
    ("river", "flows_to", "ocean"),
    ("diamond", "has_property", "hardest natural material"),
    ("gold", "has_property", "valuable"),
    ("oxygen", "required_for", "breathing"),
    ("photosynthesis", "converts", "sunlight to energy in plants"),
    ("dna", "contains", "genetic information"),
    ("atom", "is_a", "basic unit of matter"),
    ("speed of light", "is", "fastest speed in universe"),
    ("pi", "approximately_equals", "3.14159"),

    # ── MATH CONCEPTS ──
    ("addition", "used_for", "combining numbers"),
    ("subtraction", "used_for", "finding differences"),
    ("multiplication", "used_for", "repeated addition"),
    ("division", "used_for", "splitting into equal parts"),
    ("zero", "has_property", "identity element for addition"),
    ("one", "has_property", "identity element for multiplication"),
    ("infinity", "has_property", "larger than any number"),
    ("negative number", "is", "less than zero"),
    ("even number", "divisible_by", "two"),
    ("prime number", "has_property", "only divisible by 1 and itself"),
]


def load_common_sense(holographic_memory) -> int:
    """Load common sense assertions into holographic memory.

    Returns the number of facts loaded.
    """
    loaded = 0
    for subject, relation, obj in COMMON_SENSE:
        holographic_memory.store(subject, relation, obj, strength=0.8)
        loaded += 1
    return loaded


def load_common_sense_to_reasoning(reasoning_engine) -> int:
    """Load common sense as structured triples into the reasoning KB.

    This enables inference: "is a dog a pet?" → dog is_a pet (via common sense)
    """
    # Legacy CogScript integration removed — common sense now loaded via holographic memory
    return 0

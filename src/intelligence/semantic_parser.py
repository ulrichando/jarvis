"""Semantic Parser — deep sentence understanding without LLMs.

Goes beyond pattern matching to actually UNDERSTAND sentence structure:
1. Dependency parsing (who did what to whom)
2. Coreference resolution ("he" → "Ulrich", "it" → "the project")
3. Semantic role labeling (agent, patient, instrument, location)
4. Pragmatic inference (what the user MEANS vs what they SAID)

This gives Jarvis the ability to extract knowledge from ANY sentence,
not just ones matching predefined regex patterns.

Uses lightweight heuristic parsing — no spaCy or NLTK required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SemanticFrame:
    """A semantic role frame extracted from a sentence."""
    verb: str = ""
    agent: str = ""          # Who/what performs the action
    patient: str = ""        # Who/what is affected
    instrument: str = ""     # With what
    location: str = ""       # Where
    time: str = ""           # When
    purpose: str = ""        # Why / for what
    manner: str = ""         # How
    source_text: str = ""


@dataclass
class CoreferenceChain:
    """A chain of mentions referring to the same entity."""
    entity: str                    # The resolved entity name
    mentions: list[str] = field(default_factory=list)  # All references to it


class SemanticParser:
    """Extracts deep meaning from sentences using heuristic parsing.

    Three-stage pipeline:
    1. Shallow parse — identify noun phrases, verb phrases, prep phrases
    2. Semantic roles — who did what to whom, where, when, how
    3. Coreference — resolve pronouns and references
    """

    def __init__(self):
        # Track conversation context for coreference
        self._recent_entities: list[str] = []
        self._entity_gender: dict[str, str] = {}  # name → "male"/"female"/"neutral"

    def parse(self, text: str) -> list[SemanticFrame]:
        """Extract semantic frames from text."""
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        frames = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 3:
                continue
            frame = self._parse_sentence(sentence)
            if frame.verb or frame.agent or frame.patient:
                frames.append(frame)
        return frames

    def resolve_coreference(self, text: str) -> str:
        """Replace pronouns with their referents.

        "Ulrich likes Python. He uses it every day."
        → "Ulrich likes Python. Ulrich uses Python every day."
        """
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        resolved = []

        for sentence in sentences:
            # Track entities mentioned in this sentence
            entities = self._extract_noun_phrases(sentence)
            for entity in entities:
                if entity[0].isupper() and len(entity) > 1:
                    self._recent_entities.append(entity)
                    # Simple gender inference
                    if entity.lower() in _MALE_NAMES:
                        self._entity_gender[entity] = "male"
                    elif entity.lower() in _FEMALE_NAMES:
                        self._entity_gender[entity] = "female"

            # Resolve pronouns
            resolved_sentence = sentence
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "he", "male")
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "she", "female")
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "it", "neutral")
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "they", None)
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "him", "male")
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "her", "female")
            resolved_sentence = self._resolve_pronoun(resolved_sentence, "them", None)

            resolved.append(resolved_sentence)

        return " ".join(resolved)

    def extract_relations(self, text: str) -> list[tuple[str, str, str]]:
        """Extract (subject, relation, object) triples from ANY sentence.

        Goes beyond regex patterns by using syntactic heuristics:
        1. Find the main verb
        2. Subject is what comes before the verb
        3. Object is what comes after the verb
        4. Prepositions introduce additional relations
        """
        # First resolve coreferences
        resolved = self.resolve_coreference(text)
        frames = self.parse(resolved)
        triples = []

        for frame in frames:
            # Main relation: agent -[verb]-> patient
            if frame.agent and frame.verb and frame.patient:
                triples.append((frame.agent, frame.verb, frame.patient))
            elif frame.agent and frame.verb:
                triples.append((frame.agent, frame.verb, ""))

            # Location relation
            if frame.agent and frame.location:
                triples.append((frame.agent, "located_in", frame.location))

            # Instrument relation
            if frame.agent and frame.instrument:
                triples.append((frame.agent, "uses", frame.instrument))

            # Purpose relation
            if frame.agent and frame.purpose:
                triples.append((frame.agent, "for", frame.purpose))

        return triples

    def _parse_sentence(self, sentence: str) -> SemanticFrame:
        """Parse a single sentence into a semantic frame."""
        frame = SemanticFrame(source_text=sentence)
        s = sentence.strip().rstrip(".!?")
        words = s.split()

        if not words:
            return frame

        # Find the main verb (first verb-like word after initial noun phrase)
        verb_idx, verb = self._find_main_verb(words)
        if verb_idx >= 0:
            frame.verb = verb

            # Agent = everything before the verb (simplified)
            agent_words = words[:verb_idx]
            frame.agent = self._clean_phrase(" ".join(agent_words))

            # Everything after the verb
            rest = words[verb_idx + 1:]
            rest_text = " ".join(rest)

            # Extract prepositional phrases
            preps = self._extract_prepositions(rest_text)
            for prep, obj in preps:
                if prep in ("in", "at", "on", "near", "inside", "outside"):
                    frame.location = obj
                elif prep in ("with", "using"):
                    frame.instrument = obj
                elif prep in ("for", "to"):
                    frame.purpose = obj
                elif prep in ("from", "since"):
                    frame.time = obj
                elif prep in ("by"):
                    if not frame.manner:
                        frame.manner = obj
                # Remove the prep phrase from rest
                rest_text = rest_text.replace(f"{prep} {obj}", "").strip()

            # Patient = remaining text after verb (minus prep phrases)
            frame.patient = self._clean_phrase(rest_text)

        return frame

    def _find_main_verb(self, words: list[str]) -> tuple[int, str]:
        """Find the main verb in a word list."""
        # Skip initial determiners and adjectives
        for i, word in enumerate(words):
            w = word.lower().rstrip(".,!?;:")
            if w in _COMMON_VERBS or w.endswith("ed") or w.endswith("ing") or w.endswith("es"):
                if i > 0:  # Must have something before it (the subject)
                    return i, w
                elif w in ("is", "are", "was", "were", "has", "have", "had",
                           "does", "do", "did", "can", "could", "will", "would"):
                    # Auxiliary at start — find the next verb
                    for j in range(i + 1, len(words)):
                        w2 = words[j].lower().rstrip(".,!?;:")
                        if w2 in _COMMON_VERBS or w2.endswith("ed") or w2.endswith("ing"):
                            return j, f"{w} {w2}"
                    return i, w
        return -1, ""

    def _extract_prepositions(self, text: str) -> list[tuple[str, str]]:
        """Extract prepositional phrases: (preposition, object)."""
        preps = []
        for m in re.finditer(
            r'\b(in|at|on|with|for|to|from|by|near|using|about|during|before|after|since|inside|outside)\s+(.+?)(?:\s+(?:in|at|on|with|for|to|from|by|near|using|about)|\s*$)',
            text, re.I
        ):
            preps.append((m.group(1).lower(), m.group(2).strip()))
        # Simpler fallback
        if not preps:
            for m in re.finditer(
                r'\b(in|at|on|with|for|to|from|by|using)\s+(\w[\w\s]{1,30}?)(?:\.|,|$)',
                text, re.I
            ):
                preps.append((m.group(1).lower(), m.group(2).strip()))
        return preps

    def _extract_noun_phrases(self, text: str) -> list[str]:
        """Extract noun phrases (simplified — capitalized words and determiners + nouns)."""
        # Proper nouns (capitalized)
        proper = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', text)
        # "the/a/an + adjective? + noun" patterns
        det_noun = re.findall(r'\b(?:the|a|an)\s+(?:\w+\s+)?(\w+)\b', text.lower())
        return proper + det_noun

    def _resolve_pronoun(self, sentence: str, pronoun: str,
                         gender: str | None) -> str:
        """Replace a pronoun with the most recent matching entity."""
        pattern = re.compile(r'\b' + pronoun + r'\b', re.I)
        if not pattern.search(sentence):
            return sentence

        # Find the most recent entity matching the gender
        for entity in reversed(self._recent_entities[-10:]):
            if gender is None:
                # "they/them" — use most recent plural or unknown
                return pattern.sub(entity, sentence, count=1)
            entity_gender = self._entity_gender.get(entity, "neutral")
            if entity_gender == gender:
                return pattern.sub(entity, sentence, count=1)

        # Fallback: use most recent entity
        if self._recent_entities:
            return pattern.sub(self._recent_entities[-1], sentence, count=1)

        return sentence

    @staticmethod
    def _clean_phrase(text: str) -> str:
        """Clean a phrase — remove leading determiners, extra spaces."""
        text = text.strip().rstrip(".,!?;:")
        # Remove leading articles
        text = re.sub(r'^(the|a|an)\s+', '', text, flags=re.I)
        return text.strip()


# Common verb list for verb detection
_COMMON_VERBS = {
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "having",
    "does", "do", "did", "doing",
    "says", "said", "say", "saying",
    "goes", "go", "went", "going", "gone",
    "gets", "get", "got", "getting",
    "makes", "make", "made", "making",
    "knows", "know", "knew", "knowing", "known",
    "thinks", "think", "thought", "thinking",
    "takes", "take", "took", "taking", "taken",
    "sees", "see", "saw", "seeing", "seen",
    "comes", "come", "came", "coming",
    "wants", "want", "wanted", "wanting",
    "gives", "give", "gave", "giving", "given",
    "uses", "use", "used", "using",
    "finds", "find", "found", "finding",
    "tells", "tell", "told", "telling",
    "asks", "ask", "asked", "asking",
    "works", "work", "worked", "working",
    "seems", "seem", "seemed", "seeming",
    "feels", "feel", "felt", "feeling",
    "tries", "try", "tried", "trying",
    "leaves", "leave", "left", "leaving",
    "calls", "call", "called", "calling",
    "needs", "need", "needed", "needing",
    "becomes", "become", "became", "becoming",
    "keeps", "keep", "kept", "keeping",
    "lets", "let", "letting",
    "begins", "begin", "began", "beginning",
    "shows", "show", "showed", "showing", "shown",
    "hears", "hear", "heard", "hearing",
    "plays", "play", "played", "playing",
    "runs", "run", "ran", "running",
    "moves", "move", "moved", "moving",
    "lives", "live", "lived", "living",
    "believes", "believe", "believed", "believing",
    "brings", "bring", "brought", "bringing",
    "happens", "happen", "happened", "happening",
    "writes", "write", "wrote", "writing", "written",
    "provides", "provide", "provided", "providing",
    "sits", "sit", "sat", "sitting",
    "stands", "stand", "stood", "standing",
    "loses", "lose", "lost", "losing",
    "pays", "pay", "paid", "paying",
    "meets", "meet", "met", "meeting",
    "includes", "include", "included", "including",
    "continues", "continue", "continued", "continuing",
    "sets", "set", "setting",
    "learns", "learn", "learned", "learning",
    "changes", "change", "changed", "changing",
    "leads", "lead", "led", "leading",
    "understands", "understand", "understood", "understanding",
    "watches", "watch", "watched", "watching",
    "follows", "follow", "followed", "following",
    "stops", "stop", "stopped", "stopping",
    "creates", "create", "created", "creating",
    "speaks", "speak", "spoke", "speaking", "spoken",
    "reads", "read", "reading",
    "allows", "allow", "allowed", "allowing",
    "adds", "add", "added", "adding",
    "opens", "open", "opened", "opening",
    "walks", "walk", "walked", "walking",
    "wins", "win", "won", "winning",
    "offers", "offer", "offered", "offering",
    "remembers", "remember", "remembered", "remembering",
    "loves", "love", "loved", "loving",
    "considers", "consider", "considered", "considering",
    "appears", "appear", "appeared", "appearing",
    "buys", "buy", "bought", "buying",
    "waits", "wait", "waited", "waiting",
    "serves", "serve", "served", "serving",
    "dies", "die", "died", "dying",
    "sends", "send", "sent", "sending",
    "expects", "expect", "expected", "expecting",
    "builds", "build", "built", "building",
    "stays", "stay", "stayed", "staying",
    "falls", "fall", "fell", "falling", "fallen",
    "cuts", "cut", "cutting",
    "reaches", "reach", "reached", "reaching",
    "kills", "kill", "killed", "killing",
    "remains", "remain", "remained", "remaining",
    "suggests", "suggest", "suggested", "suggesting",
    "raises", "raise", "raised", "raising",
    "passes", "pass", "passed", "passing",
    "sells", "sell", "sold", "selling",
    "requires", "require", "required", "requiring",
    "reports", "report", "reported", "reporting",
    "decides", "decide", "decided", "deciding",
    "pulls", "pull", "pulled", "pulling",
    "develops", "develop", "developed", "developing",
    "causes", "cause", "caused", "causing",
    "contains", "contain", "contained", "containing",
    "stores", "store", "stored", "storing",
    "scans", "scan", "scanned", "scanning",
    "installs", "install", "installed", "installing",
    "connects", "connect", "connected", "connecting",
    "starts", "start", "started", "starting",
    "encrypts", "encrypt", "encrypted", "encrypting",
    "hacks", "hack", "hacked", "hacking",
    "deploys", "deploy", "deployed", "deploying",
}

# Common names for gender inference (small set)
_MALE_NAMES = {
    "ulrich", "john", "james", "robert", "michael", "david", "richard",
    "joseph", "thomas", "charles", "daniel", "matthew", "mark", "paul",
    "steven", "andrew", "kevin", "brian", "george", "edward", "peter",
    "jack", "alex", "sam", "ben", "chris", "nick", "tom", "mike",
}

_FEMALE_NAMES = {
    "mary", "patricia", "jennifer", "linda", "elizabeth", "barbara",
    "susan", "jessica", "sarah", "karen", "nancy", "lisa", "betty",
    "helen", "sandra", "donna", "carol", "ruth", "sharon", "michelle",
    "laura", "anna", "emma", "jane", "alice", "kate", "rachel", "amy",
}

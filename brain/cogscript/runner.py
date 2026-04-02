"""CogScript Brain Runner for JARVIS.

Two modes:
  1. Bridged (default) — CogScript drives Jarvis's real Python subsystems
  2. Standalone — CogScript runs with its own mock/stub subsystems

Usage:
    # From jarvis root:
    python brain/cogscript/runner.py                    # bridged mode
    python brain/cogscript/runner.py --standalone       # standalone mode
    python brain/cogscript/runner.py --input "hello"    # single input

    # As a module:
    from brain.cogscript.runner import JarvisCogBrain
    brain = JarvisCogBrain(bridged=True)
    brain.run()
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path

# Ensure both jarvis root and cogscript are importable
_jarvis_root = Path(__file__).resolve().parent.parent.parent
_cogscript_root = _jarvis_root.parent / "CogScript"

for p in [str(_jarvis_root), str(_cogscript_root)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_env():
    """Load Jarvis .env file into os.environ."""
    import os
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


class JarvisCogBrain:
    """JARVIS brain powered by CogScript.

    Persistent: subsystem state (memory, knowledge, episodes) survives
    across run() calls. The brain accumulates knowledge over its lifetime.
    """

    def __init__(self, bridged: bool = True, cog_file: str | None = None):
        _load_env()
        self.bridged = bridged
        self.cog_file = cog_file or str(Path(__file__).parent / "jarvis.cog")
        self._program = None
        self._interpreter = None
        self._booted = False
        self._persistent_regions = {}

    def load(self):
        """Parse the .cog brain file and optionally patch subsystems."""
        from cogscript.lang.lexer import tokenize
        from cogscript.lang.parser import parse

        source = Path(self.cog_file).read_text()
        self._program = parse(tokenize(source))

        # Set source directory on brain nodes so includes resolve correctly
        cog_dir = str(Path(self.cog_file).resolve().parent)
        for brain_decl in self._program.brains:
            brain_decl._source_dir = cog_dir

        if self.bridged:
            from brain.cogscript.bridge import patch_subsystem_map
            patch_subsystem_map()

    def _boot(self):
        """First run: create regions, seed knowledge, save persistent refs."""
        from cogscript.runtime.interpreter import Interpreter
        from cogscript.runtime.values import wrap

        interp = Interpreter()
        interp.env.define("user_input", wrap(""))
        interp.interpret(self._program)

        # Seed foundational knowledge into the brain's regions
        brain = list(interp.brains.values())[0]
        self._seed_knowledge(brain)

        # Save persistent references to all regions
        for name, sub in brain.regions.items():
            self._persistent_regions[name] = sub

        self._booted = True

    def _seed_knowledge(self, brain):
        """Seed foundational knowledge into the brain's LTM and reasoning engine."""
        ltm = brain.regions.get('knowledge')
        mind = brain.regions.get('mind')
        if not ltm:
            return

        from brain.cogscript.seed_knowledge import build_knowledge
        from cogscript.runtime.values import wrap

        seeded_ltm, seeded_mind = build_knowledge()

        if hasattr(seeded_ltm, '_entries'):
            for key, entry in seeded_ltm._entries.items():
                tags = list(entry.tags) if hasattr(entry, 'tags') else []
                ltm.remember(wrap(entry.value), tags=tags)

        if mind and hasattr(seeded_mind, 'kb') and hasattr(seeded_mind.kb, 'facts'):
            for fact in seeded_mind.kb.facts:
                mind.kb.assert_fact(fact)

    def _make_interpreter_with_persistent_regions(self, user_input: str):
        """Create an interpreter that reuses persistent regions instead of creating new ones."""
        from cogscript.runtime.interpreter import Interpreter
        from cogscript.runtime.values import wrap

        interp = Interpreter()

        if user_input:
            interp.env.define("user_input", wrap(user_input))

        # Monkey-patch _exec_region to reuse persistent regions
        persistent = self._persistent_regions
        original_exec_region = interp._exec_region

        def patched_exec_region(node, brain):
            if node.name in persistent:
                # Reuse existing persistent region (don't re-create)
                brain.register_region(node.name, persistent[node.name])
                interp.env.define(node.name, wrap(persistent[node.name]))
            else:
                original_exec_region(node, brain)

        interp._exec_region = patched_exec_region

        # Monkey-patch _exec_brain to skip initialize() for persistent regions
        original_exec_brain = interp._exec_brain

        def patched_exec_brain(node):
            from cogscript.runtime.brain_instance import BrainInstance
            from cogscript.lang import ast_nodes as ast

            brain = BrainInstance(name=node.name, ast_node=node)
            interp.brains[node.name] = brain
            interp.current_brain = brain

            # Resolve includes before registering members
            resolved_body = interp._resolve_includes(node.body, node)

            # Register all regions, pathways, and on-handlers
            for member in resolved_body:
                if isinstance(member, ast.RegionDecl):
                    patched_exec_region(member, brain)
                elif isinstance(member, ast.PathwayDecl):
                    brain.register_pathway(member.name, member)
                elif isinstance(member, ast.OnDecl):
                    brain.register_on(member.trigger, member)

            # Initialize ONLY non-persistent (new) subsystems
            for name, subsystem in brain.regions.items():
                if name not in persistent:
                    subsystem.initialize()

            # Fire "on start" handler
            if 'start' in brain.on_handlers:
                interp._exec_block(brain.on_handlers['start'].body)

        interp._exec_brain = patched_exec_brain
        return interp

    def run(self, user_input: str | None = None):
        """Run the brain's think cycle.

        On first run, boots and seeds knowledge.
        Persistent regions survive across calls so knowledge accumulates.
        """
        if not self._program:
            self.load()

        if not self._booted:
            self._boot()

        # Create interpreter with persistent regions injected
        interp = self._make_interpreter_with_persistent_regions(user_input or "")

        # Interpret (reuses persistent regions, runs on start pathways)
        interp.interpret(self._program)

        # Update persistent refs with any changes
        brain = list(interp.brains.values())[0]
        for name, sub in brain.regions.items():
            self._persistent_regions[name] = sub

        self._interpreter = interp
        return interp.output

    def get_response(self) -> str | None:
        """Get the brain's response after a think cycle.

        Priority:
        1. Perception context (if user asks about seeing/hearing)
        2. Reasoning engine KB (structured triples)
        3. LTM recall (plain text facts)
        """
        wm = self._persistent_regions.get('focus')
        if not wm:
            return None

        # Get the user's original query for relevance matching
        user_result = wm.recall(query="user_input")
        user_query = ""
        if user_result and user_result.value:
            user_query = str(user_result.value).lower()

        # Check if user is asking about perception (what do you see/hear)
        perception_resp = self._check_perception_query(wm, user_query)
        if perception_resp:
            return perception_resp

        # Try reasoning engine answer
        result = wm.recall(query="response")
        if result and result.value:
            raw = result.value
            if isinstance(raw, dict):
                facts = raw.get('facts', [])
                mode = raw.get('mode', '')

                if mode != 'insufficient' and facts:
                    best = self._find_best_fact(facts, user_query)
                    if best:
                        return best

        # Fall back to direct LTM search with targeted keywords
        ltm = self._persistent_regions.get('knowledge')
        if ltm and user_query:
            from cogscript.runtime.values import CogValue
            stop_words = {'what', 'is', 'are', 'the', 'a', 'an', 'of', 'to', 'in',
                          'for', 'and', 'or', 'does', 'do', 'how', 'why', 'who',
                          'where', 'when', 'tell', 'me', 'about', 'can', 'you'}
            terms = [w for w in user_query.split() if w not in stop_words and len(w) > 1]

            all_results = []
            for term in terms:
                result = ltm.recall(query=term, top_k=10)
                raw = result.value if isinstance(result, CogValue) else result
                if isinstance(raw, list):
                    for item in raw:
                        v = str(item.value if isinstance(item, CogValue) else item)
                        if v not in all_results:
                            all_results.append(v)
                elif raw:
                    v = str(raw)
                    if v not in all_results:
                        all_results.append(v)

            if all_results:
                best = self._find_best_fact(all_results, user_query)
                if best:
                    return best

        return None

    @staticmethod
    def _check_perception_query(wm, user_query: str) -> str | None:
        """Check if the user is asking about what Jarvis sees or hears.

        Returns a natural language description from the see/hear pathways,
        or None if this isn't a perception query.
        """
        from cogscript.runtime.values import CogValue

        vision_words = {'see', 'seeing', 'look', 'looking', 'camera', 'eyes',
                        'scene', 'view', 'vision', 'front', 'visible', 'watch',
                        'observe', 'spot', 'notice', 'detect', 'describe'}
        hearing_words = {'hear', 'hearing', 'listen', 'listening', 'audio',
                         'ears', 'sound', 'noise', 'voice', 'microphone', 'mic',
                         'said', 'saying'}

        query_words = set(user_query.split())
        asks_vision = bool(query_words & vision_words)
        asks_hearing = bool(query_words & hearing_words)

        if not asks_vision and not asks_hearing:
            return None

        parts = []

        if asks_vision:
            scene = wm.recall(query="scene")
            if scene and scene.value:
                raw = scene.value
                if isinstance(raw, CogValue):
                    raw = raw.value
                desc = str(raw)
                if desc and 'mock' not in desc.lower() and len(desc) > 3:
                    parts.append(desc)
                else:
                    parts.append("I can't see anything right now - my camera might not be active.")
            else:
                parts.append("I can't see anything right now - my camera might not be active.")

        if asks_hearing:
            heard = wm.recall(query="heard")
            if heard and heard.value:
                raw = heard.value
                if isinstance(raw, CogValue):
                    raw = raw.value
                text = str(raw)
                if text and 'mock' not in text.lower() and len(text) > 3:
                    parts.append(f"I heard: {text}")
                else:
                    parts.append("I don't hear anything right now.")
            else:
                parts.append("I don't hear anything right now.")

        return " ".join(parts) if parts else None

    @staticmethod
    def _find_best_fact(facts: list, query: str) -> str | None:
        """Find the fact most relevant to the query by keyword overlap.

        Filters out:
        - Facts that are just the query echoed back
        - Reasoning engine metadata dicts
        - Insufficiency markers
        """
        if not facts or not query:
            return str(facts[0]) if facts else None

        stop_words = {'what', 'is', 'are', 'the', 'a', 'an', 'of', 'to', 'in',
                      'for', 'and', 'or', 'does', 'do', 'how', 'why', 'who',
                      'where', 'when', 'has_property', 'is_a', 'part_of',
                      'tell', 'me', 'about', 'can', 'you', 'your'}
        query_terms = {w for w in query.split() if w not in stop_words and len(w) > 1}

        best_score = -1
        best_fact = None

        for fact in facts:
            fact_str = str(fact)
            fact_lower = fact_str.lower().strip()

            # Skip facts that are just the query echoed back
            if fact_lower == query.strip() or fact_lower.rstrip('?') == query.strip().rstrip('?'):
                continue
            # Skip reasoning engine internal dicts
            if fact_lower.startswith('{') or 'insufficient' in fact_lower:
                continue
            # Skip very short/empty facts
            if len(fact_lower) < 3:
                continue

            score = sum(1 for term in query_terms if term in fact_lower)

            # Bonus for facts that look like definitions (contain "is", "means")
            if ' is ' in fact_lower or ' means ' in fact_lower:
                score += 0.5

            if score > best_score:
                best_score = score
                best_fact = fact_str

        return best_fact if best_score > 0 else None


def main():
    parser = argparse.ArgumentParser(description="JARVIS CogScript Brain Runner")
    parser.add_argument("--standalone", action="store_true", help="Run without Jarvis bridges")
    parser.add_argument("--input", "-i", type=str, help="User input to process")
    parser.add_argument("--cog", type=str, help="Path to .cog brain file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    brain = JarvisCogBrain(
        bridged=not args.standalone,
        cog_file=args.cog,
    )

    if args.verbose:
        print(f"Mode: {'bridged' if brain.bridged else 'standalone'}")
        print(f"Brain: {brain.cog_file}")
        print()

    if args.input:
        brain.run(user_input=args.input)
        response = brain.get_response()
        if response:
            print(f"\nJARVIS: {response}")
        else:
            print("\nJARVIS: I don't have enough information to respond.")
    else:
        brain.run()


if __name__ == "__main__":
    main()

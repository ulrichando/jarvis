"""Bridge — connects CogScript regions to Jarvis's real Python subsystems.

Maps:
    CogScript Region            ->  Jarvis Python Class
    ─────────────────────────────────────────────────────
    Perception("camera")        ->  brain.vision.camera
    Perception("microphone")    ->  brain.speech.stt
    WorkingMemory               ->  CogScript native (better than dict state)
    LongTermMemory              ->  brain.memory.lattice.NeuralLattice
    EpisodicMemory              ->  brain.memory.store.MemoryStore (SQLite)
    ReasoningEngine             ->  brain.reasoning.reason.ReasoningEngine
    LLMBackend("groq")          ->  brain.reasoning.groq_client.GroqReasoner
    LLMBackend("claude")        ->  brain.reasoning.claude_client.ClaudeClient
    ActiveLearning              ->  brain.intelligence.curiosity.CuriosityEngine
    SelfEvolution               ->  brain.evolution.engine.EvolutionEngine
    PracticeLoop                ->  brain.intelligence.reinforcement.ReinforcementLearner
"""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Any

# Ensure jarvis root is importable
_jarvis_root = Path(__file__).resolve().parent.parent.parent
if str(_jarvis_root) not in sys.path:
    sys.path.insert(0, str(_jarvis_root))

from cogscript.runtime.values import CogValue, CogMemoryRef, wrap
from cogscript.subsystems.perception import PerceptionSubsystem
from cogscript.subsystems.long_term_memory import LongTermMemorySubsystem
from cogscript.subsystems.episodic_memory import EpisodicMemorySubsystem
from cogscript.subsystems.llm_backend import LLMBackendSubsystem
from cogscript.subsystems.self_evolution import SelfEvolutionSubsystem


# ── LongTermMemory -> NeuralLattice ──

class LatticeBridge(LongTermMemorySubsystem):
    """Wraps Jarvis's NeuralLattice as a CogScript LongTermMemory."""

    subsystem_type = "LongTermMemory"

    def __init__(self, backend: str = "dict", **config):
        super().__init__(backend=backend, **config)
        self._lattice = None

    def initialize(self):
        try:
            from brain.memory.lattice.lattice import NeuralLattice
            from brain.config import DATA_DIR
            self._lattice = NeuralLattice(str(DATA_DIR / "lattice"))
        except Exception:
            self._lattice = None

    def shutdown(self):
        if self._lattice:
            try:
                self._lattice.save()
            except Exception:
                pass

    def remember(self, value: CogValue, **kwargs) -> Any:
        raw = value.value if isinstance(value, CogValue) else value
        text = str(raw)
        tags = kwargs.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        if self._lattice:
            try:
                node_id = self._lattice.absorb(text, tags=tags)
                return CogValue(value=node_id, confidence=1.0)
            except Exception:
                pass
        return CogValue(value=text, confidence=0.5)

    def recall(self, query: str | None = None, top_k: int = 5, **kwargs) -> CogValue:
        if self._lattice and query:
            try:
                results = self._lattice.recall(str(query), top_k=top_k)
                items = []
                for r in results:
                    content = r.content if hasattr(r, 'content') else str(r)
                    items.append(content)
                return wrap(items)
            except Exception:
                pass
        return wrap([])

    def forget(self, query: str):
        pass  # Lattice uses decay


# ── EpisodicMemory -> MemoryStore (SQLite) ──

class SQLiteBridge(EpisodicMemorySubsystem):
    """Wraps Jarvis's SQLite MemoryStore as a CogScript EpisodicMemory."""

    subsystem_type = "EpisodicMemory"

    def __init__(self, **config):
        super().__init__(**config)
        self._store = None

    def initialize(self):
        try:
            from brain.memory.store import MemoryStore
            self._store = MemoryStore()
        except Exception:
            self._store = None

    def shutdown(self):
        self._store = None

    def record(self, action: str, data: Any, **kwargs):
        if self._store:
            try:
                self._store.append(role="system", content=f"[{action}] {data}")
            except Exception:
                pass

    def recall(self, **kwargs) -> CogValue:
        limit = kwargs.get("limit", 10)
        if self._store:
            try:
                history = self._store.get_recent(limit=limit)
                return wrap(history)
            except Exception:
                pass
        return wrap([])


# ── LLMBackend -> GroqReasoner / ClaudeClient ──

class LLMBridge(LLMBackendSubsystem):
    """Wraps Jarvis's LLM clients as a CogScript LLMBackend."""

    subsystem_type = "LLMBackend"

    def __init__(self, provider: str = "groq", **config):
        super().__init__(provider=provider, **config)
        self.provider = provider
        self._client = None

    def initialize(self):
        try:
            if self.provider == "groq":
                from brain.reasoning.groq_client import GroqReasoner
                self._client = GroqReasoner()
            elif self.provider == "claude":
                from brain.reasoning.claude_client import ClaudeClient
                self._client = ClaudeClient()
        except Exception:
            self._client = None

    def shutdown(self):
        self._client = None

    def reason(self, given: list[CogValue], query: str) -> CogValue:
        context = "\n".join(str(g.value if isinstance(g, CogValue) else g) for g in given)
        full_query = f"Context:\n{context}\n\nQuestion: {query}" if context.strip() else query

        if self._client:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        response = pool.submit(
                            asyncio.run, self._client.chat(full_query)
                        ).result(timeout=30)
                else:
                    response = loop.run_until_complete(self._client.chat(full_query))
                return CogValue(value=response, confidence=0.9)
            except Exception as e:
                return CogValue(value=f"LLM error: {e}", confidence=0.1)

        return CogValue(value=f"[No LLM client] {query}", confidence=0.1)

    def chat(self, message: str, **kwargs) -> CogValue:
        return self.reason([], message)


# ── Perception -> Vision / Speech ──

class VisionBridge(PerceptionSubsystem):
    """Wraps Jarvis's camera/screen capture as CogScript Perception.

    Camera: captures a frame, loads it as a numpy array, and runs
    local CV analysis (face detection, colors, brightness, scene type)
    to produce a natural language description.

    Microphone: uses VAD to listen for speech, then transcribes with Whisper.

    Screen: takes a screenshot and loads it as a numpy array.
    """

    subsystem_type = "Perception"

    def __init__(self, source: str = "camera", **config):
        super().__init__(source=source, **config)
        self.source = source

    def initialize(self):
        pass

    def shutdown(self):
        pass

    def perceive(self) -> CogValue:
        import time

        if self.source in ("camera", "video"):
            try:
                import cv2
                from brain.vision.camera import capture_frame
                from brain.vision.describe import analyze_image, describe_analysis

                path = capture_frame()
                if path:
                    # Load as numpy array for CogScript pipeline ops
                    img = cv2.imread(path)
                    if img is not None:
                        h, w = img.shape[:2]
                        # Run local CV analysis for a natural description
                        analysis = analyze_image(path)
                        description = describe_analysis(analysis)
                        return CogValue(
                            value={
                                "type": "frame",
                                "data": img,
                                "width": w,
                                "height": h,
                                "channels": 3,
                                "timestamp": time.time(),
                                "description": description,
                                "analysis": analysis,
                            },
                            confidence=0.95,
                        )
            except Exception:
                pass

        elif self.source in ("microphone", "audio"):
            try:
                from brain.speech.vad import listen_until_silence
                from brain.speech.stt import transcribe_audio

                audio = listen_until_silence(timeout=5)
                if audio is not None:
                    text = transcribe_audio(audio, 16000)
                    if text:
                        return CogValue(
                            value={
                                "type": "audio",
                                "transcription": {"text": text},
                                "data": audio,
                                "sample_rate": 16000,
                                "timestamp": time.time(),
                            },
                            confidence=0.95,
                        )
                    # Audio detected but no speech
                    return CogValue(
                        value={
                            "type": "audio",
                            "transcription": {"text": ""},
                            "timestamp": time.time(),
                        },
                        confidence=0.3,
                    )
            except Exception:
                pass

        elif self.source == "screen":
            try:
                import cv2
                from brain.vision.screen import take_screenshot
                from brain.vision.describe import analyze_image, describe_analysis

                path = take_screenshot("full")
                if path:
                    img = cv2.imread(path)
                    if img is not None:
                        h, w = img.shape[:2]
                        analysis = analyze_image(path)
                        description = describe_analysis(analysis)
                        return CogValue(
                            value={
                                "type": "screen",
                                "data": img,
                                "width": w,
                                "height": h,
                                "timestamp": time.time(),
                                "description": description,
                                "analysis": analysis,
                            },
                            confidence=0.95,
                        )
            except Exception:
                pass

        # Fallback — no hardware available
        return CogValue(
            value={"type": self.source, "data": None, "mock": True, "timestamp": time.time()},
            confidence=0.3,
        )


# ── Evolution -> EvolutionEngine ──

class EvolutionBridge(SelfEvolutionSubsystem):
    """Wraps Jarvis's EvolutionEngine + CogScript's AST evolution."""

    subsystem_type = "SelfEvolution"

    def __init__(self, strategy: str = "genetic", **config):
        super().__init__(strategy=strategy, **config)
        self._engine = None

    def initialize(self):
        try:
            from brain.evolution.engine import EvolutionEngine
            from brain.evolution.telemetry import Telemetry
            self._engine = EvolutionEngine(Telemetry())
        except Exception:
            self._engine = None

    def shutdown(self):
        self._engine = None

    def evolve(self, source_ast, fitness_score: float, mutate_target: str = "pathway",
               generations: int = 10) -> CogValue:
        # Trigger Jarvis's shortcut evolution in background
        if self._engine:
            try:
                asyncio.get_event_loop().run_until_complete(self._engine.evolve(days=1))
            except Exception:
                pass

        # Use CogScript's native AST evolution
        from cogscript.subsystems.self_evolution import SelfEvolutionSubsystem
        native = SelfEvolutionSubsystem(strategy="genetic")
        native.initialize()
        return native.evolve(source_ast, fitness_score, mutate_target, generations)


# ── Factory ──

def patch_subsystem_map():
    """Replace CogScript's default subsystems with Jarvis bridges."""
    from cogscript.runtime.interpreter import SUBSYSTEM_MAP

    SUBSYSTEM_MAP['Perception'] = VisionBridge
    SUBSYSTEM_MAP['LongTermMemory'] = LatticeBridge
    SUBSYSTEM_MAP['EpisodicMemory'] = SQLiteBridge
    SUBSYSTEM_MAP['LLMBackend'] = LLMBridge
    SUBSYSTEM_MAP['SelfEvolution'] = EvolutionBridge
    # Keep CogScript native for these (they're better):
    # WorkingMemory, ReasoningEngine, ActiveLearning, PracticeLoop

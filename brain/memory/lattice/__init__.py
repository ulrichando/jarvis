"""Neural Memory Lattice — JARVIS's artificial hippocampus.

A brain-inspired memory system where knowledge is stored as interconnected
nodes with weighted associations that strengthen with use and decay over time.

Key concepts:
- MemoryNode: A single unit of knowledge (fact, episode, concept)
- Synapse: A weighted connection between two nodes
- Decay: Unused memories fade, frequently accessed ones grow stronger
- Compression: Clusters of related nodes auto-compress into concept nodes
"""

from brain.memory.lattice.node import MemoryNode, NodeType
from brain.memory.lattice.synapse import Synapse
from brain.memory.lattice.lattice import NeuralLattice

__all__ = ["MemoryNode", "NodeType", "Synapse", "NeuralLattice"]

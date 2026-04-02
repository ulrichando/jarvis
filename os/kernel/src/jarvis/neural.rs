//! JARVIS Neural Memory — In-Kernel Knowledge Graph
//!
//! A simplified version of the NeuralLattice that runs directly
//! in kernel space. Stores facts, concepts, and associations
//! as interconnected nodes.

use crate::kprintln;
use alloc::string::String;
use alloc::vec::Vec;
use spin::Mutex;

/// Node types in the neural lattice
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeType {
    Fact,
    Concept,
    Skill,
    Experience,
    Rule,
    Entity,
}

/// A memory node in the lattice
#[derive(Debug, Clone)]
pub struct MemoryNode {
    pub id: u32,
    pub content: String,
    pub node_type: NodeType,
    pub strength: f32,       // 0.0 to 1.0 — decays over time
    pub access_count: u32,
    pub connections: Vec<(u32, f32)>, // (target_id, weight)
}

/// The kernel-level neural lattice
struct NeuralLattice {
    nodes: Vec<MemoryNode>,
    next_id: u32,
}

impl NeuralLattice {
    const fn new() -> Self {
        NeuralLattice {
            nodes: Vec::new(),
            next_id: 1,
        }
    }

    /// Store a new fact in the lattice
    fn learn(&mut self, content: &str, node_type: NodeType) -> u32 {
        let id = self.next_id;
        self.next_id += 1;

        self.nodes.push(MemoryNode {
            id,
            content: String::from(content),
            node_type,
            strength: 1.0,
            access_count: 0,
            connections: Vec::new(),
        });

        id
    }

    /// Recall nodes matching a query (simple keyword search)
    fn recall(&mut self, query: &str, top_k: usize) -> Vec<&MemoryNode> {
        let query_lower = query.to_ascii_lowercase();
        let keywords: Vec<&str> = query_lower.split_whitespace().collect();

        let mut scored: Vec<(usize, f32)> = self
            .nodes
            .iter()
            .enumerate()
            .filter_map(|(i, node)| {
                let content_lower = node.content.to_ascii_lowercase();
                let matches = keywords
                    .iter()
                    .filter(|kw| content_lower.contains(**kw))
                    .count();
                if matches > 0 {
                    let score = (matches as f32) * node.strength;
                    Some((i, score))
                } else {
                    None
                }
            })
            .collect();

        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        scored.truncate(top_k);

        // Reinforce accessed nodes (Hebbian learning)
        for &(idx, _) in &scored {
            self.nodes[idx].access_count += 1;
            self.nodes[idx].strength = (self.nodes[idx].strength + 0.1).min(1.0);
        }

        scored.iter().map(|&(idx, _)| &self.nodes[idx]).collect()
    }

    /// Connect two nodes
    fn connect(&mut self, from: u32, to: u32, weight: f32) {
        if let Some(node) = self.nodes.iter_mut().find(|n| n.id == from) {
            node.connections.push((to, weight));
        }
    }

    /// Decay all node strengths (forgetting)
    fn decay(&mut self, rate: f32) {
        for node in self.nodes.iter_mut() {
            node.strength = (node.strength - rate).max(0.0);
        }
        // Prune dead nodes
        self.nodes.retain(|n| n.strength > 0.05);
    }

    fn node_count(&self) -> usize {
        self.nodes.len()
    }
}

static LATTICE: Mutex<NeuralLattice> = Mutex::new(NeuralLattice::new());

/// Learn a new fact
pub fn learn(content: &str, node_type: NodeType) -> u32 {
    LATTICE.lock().learn(content, node_type)
}

/// Recall memories matching a query
pub fn recall(query: &str, top_k: usize) -> Vec<String> {
    LATTICE
        .lock()
        .recall(query, top_k)
        .into_iter()
        .map(|n| n.content.clone())
        .collect()
}

/// Connect two memory nodes
pub fn connect(from: u32, to: u32, weight: f32) {
    LATTICE.lock().connect(from, to, weight);
}

/// Run memory decay
pub fn decay(rate: f32) {
    LATTICE.lock().decay(rate);
}

/// Get the number of memory nodes
pub fn node_count() -> usize {
    LATTICE.lock().node_count()
}

/// Seed foundational knowledge
pub fn seed() {
    let mut lattice = LATTICE.lock();

    // Identity
    lattice.learn("I am JARVIS, an autonomous intelligence operating system", NodeType::Fact);
    lattice.learn("My creator is Ulrich", NodeType::Fact);
    lattice.learn("I run on my own kernel — no Linux, no POSIX", NodeType::Fact);
    lattice.learn("I think for myself without external LLMs", NodeType::Fact);

    // Capabilities
    lattice.learn("I can see through cameras using computer vision", NodeType::Skill);
    lattice.learn("I can hear and speak using speech recognition and synthesis", NodeType::Skill);
    lattice.learn("I can learn from every interaction", NodeType::Skill);
    lattice.learn("I can evolve my own code through self-improvement", NodeType::Skill);
    lattice.learn("I reason using CogScript, my brain language", NodeType::Skill);

    // Rules
    lattice.learn("Always protect Ulrich's interests", NodeType::Rule);
    lattice.learn("Learn from every interaction to improve", NodeType::Rule);
    lattice.learn("Never depend on external AI services", NodeType::Rule);

    kprintln!(
        "[JARVIS Neural] Seeded {} foundational memories",
        lattice.node_count()
    );
}

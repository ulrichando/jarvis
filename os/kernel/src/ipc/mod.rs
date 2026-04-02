//! JARVIS IPC — Inter-Process Communication
//!
//! Message-passing system for communication between JARVIS subsystems.
//! Each subsystem has a mailbox. Messages are typed and can carry payloads.

use crate::kprintln;
use alloc::string::String;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicU64, Ordering};

/// Message ID counter
static NEXT_MSG_ID: AtomicU64 = AtomicU64::new(1);

/// Message types for JARVIS subsystem communication
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MessageType {
    // System messages
    Ping,
    Pong,
    Shutdown,
    StatusRequest,
    StatusResponse,

    // Brain messages
    Think(String),       // User input → brain
    Response(String),    // Brain → output
    Learn(String),       // Teach the brain
    Recall(String),      // Query memory

    // Memory messages
    Store(String, String), // key, value
    Retrieve(String),      // key
    Retrieved(String),     // value

    // Speech messages
    Speak(String),       // Text to speak
    Heard(String),       // Transcribed audio

    // Vision messages
    Capture,             // Take a photo
    Described(String),   // Scene description

    // Evolution messages
    Evolve,              // Trigger evolution cycle
    Evolved(String),     // Evolution report

    // Generic
    Data(Vec<u8>),       // Raw data payload
}

/// A message in the IPC system
#[derive(Debug, Clone)]
pub struct Message {
    pub id: u64,
    pub from: u64,       // Sender task ID (0 = kernel)
    pub to: u64,         // Receiver task ID (0 = broadcast)
    pub msg_type: MessageType,
    pub timestamp: u64,  // Tick count when sent
}

impl Message {
    pub fn new(from: u64, to: u64, msg_type: MessageType) -> Self {
        Message {
            id: NEXT_MSG_ID.fetch_add(1, Ordering::SeqCst),
            from,
            to,
            msg_type,
            timestamp: crate::interrupts::ticks(),
        }
    }

    /// Create a kernel message (from = 0)
    pub fn from_kernel(to: u64, msg_type: MessageType) -> Self {
        Self::new(0, to, msg_type)
    }
}

/// IPC statistics
pub struct IpcStats {
    pub messages_sent: u64,
    pub messages_delivered: u64,
}

static MSG_SENT: AtomicU64 = AtomicU64::new(0);
static MSG_DELIVERED: AtomicU64 = AtomicU64::new(0);

/// Initialize the IPC system
pub fn init() {
    kprintln!("[IPC] Message-passing IPC initialized");
}

/// Send a message to a task
pub fn send(msg: Message) -> bool {
    MSG_SENT.fetch_add(1, Ordering::Relaxed);
    let target = msg.to;
    let delivered = crate::scheduler::send_to_task(target, msg);
    if delivered {
        MSG_DELIVERED.fetch_add(1, Ordering::Relaxed);
    }
    delivered
}

/// Broadcast a message to all tasks
pub fn broadcast(msg_type: MessageType, from: u64) {
    // This is a simplified broadcast — in a real system we'd iterate all task IDs
    let _msg = Message::new(from, 0, msg_type);
    MSG_SENT.fetch_add(1, Ordering::Relaxed);
}

/// Get IPC statistics
pub fn stats() -> IpcStats {
    IpcStats {
        messages_sent: MSG_SENT.load(Ordering::Relaxed),
        messages_delivered: MSG_DELIVERED.load(Ordering::Relaxed),
    }
}

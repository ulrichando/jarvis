//! JARVIS System Calls
//!
//! The syscall interface for JARVIS userspace (future).
//! For now, defines the syscall numbers and stubs.

/// JARVIS syscall numbers
#[derive(Debug, Clone, Copy)]
#[repr(u64)]
pub enum Syscall {
    // Process management
    Exit = 0,
    Spawn = 1,
    Yield = 2,
    Sleep = 3,
    GetPid = 4,

    // IPC
    Send = 10,
    Receive = 11,
    ReceiveTimeout = 12,

    // Memory
    Allocate = 20,
    Deallocate = 21,
    Map = 22,

    // JARVIS-specific
    Think = 100,      // Send input to brain
    Learn = 101,      // Teach the brain
    Recall = 102,     // Query memory
    Speak = 103,      // Text to speech
    Listen = 104,     // Speech to text
    See = 105,        // Capture + describe
    Evolve = 106,     // Trigger self-improvement

    // I/O
    Write = 200,      // Write to output
    Read = 201,       // Read from input
    Open = 202,
    Close = 203,
}

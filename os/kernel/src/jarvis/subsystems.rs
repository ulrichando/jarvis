//! JARVIS Subsystem Task Functions
//!
//! Each subsystem runs as a kernel task. These are the tick functions
//! called by the scheduler on each time slice.

use crate::{kprintln, fbprintln};
use crate::jarvis::neural;

/// Memory subsystem — manages the neural lattice
pub fn memory_tick() {
    // Periodic maintenance: decay old memories
    static DECAY_COUNTER: core::sync::atomic::AtomicU64 = core::sync::atomic::AtomicU64::new(0);
    let count = DECAY_COUNTER.fetch_add(1, core::sync::atomic::Ordering::Relaxed);

    // Decay every ~1000 ticks (~55 seconds at 18.2 Hz PIT)
    if count % 1000 == 0 && count > 0 {
        neural::decay(0.01);
    }
}

/// Brain subsystem — CogScript reasoning engine
///
/// In the full implementation, this would run the CogScript interpreter.
/// For now, it seeds knowledge and processes incoming think requests.
pub fn brain_tick() {
    static INITIALIZED: core::sync::atomic::AtomicBool =
        core::sync::atomic::AtomicBool::new(false);

    if !INITIALIZED.swap(true, core::sync::atomic::Ordering::SeqCst) {
        // First tick — seed foundational knowledge
        neural::seed();
        kprintln!("[JARVIS Brain] CogScript engine ready ({} memories)", neural::node_count());
    }

    // Process incoming messages (think requests, learn commands, etc.)
    // This would be driven by IPC messages from the shell
}

/// Speech subsystem — STT and TTS
///
/// In the full kernel, this would interface with audio hardware
/// through a PCI audio driver (AC97/HDA).
pub fn speech_tick() {
    static INITIALIZED: core::sync::atomic::AtomicBool =
        core::sync::atomic::AtomicBool::new(false);

    if !INITIALIZED.swap(true, core::sync::atomic::Ordering::SeqCst) {
        kprintln!("[JARVIS Speech] Audio subsystem standing by (driver pending)");
    }
}

/// Vision subsystem — camera and CV
///
/// In the full kernel, this would interface with USB cameras
/// through a USB host controller driver.
pub fn vision_tick() {
    static INITIALIZED: core::sync::atomic::AtomicBool =
        core::sync::atomic::AtomicBool::new(false);

    if !INITIALIZED.swap(true, core::sync::atomic::Ordering::SeqCst) {
        kprintln!("[JARVIS Vision] Vision subsystem standing by (driver pending)");
    }
}

/// Evolution subsystem — self-improvement
///
/// Periodically analyzes performance telemetry and generates
/// improvements. In the kernel, this would modify loaded code segments.
pub fn evolution_tick() {
    static INITIALIZED: core::sync::atomic::AtomicBool =
        core::sync::atomic::AtomicBool::new(false);

    if !INITIALIZED.swap(true, core::sync::atomic::Ordering::SeqCst) {
        kprintln!("[JARVIS Evolution] Self-improvement engine standing by");
    }
}

/// Shell subsystem — user interaction
///
/// Reads keyboard input, sends to brain, displays responses.
/// This is the primary user-facing interface in the kernel.
pub fn shell_tick() {
    static INITIALIZED: core::sync::atomic::AtomicBool =
        core::sync::atomic::AtomicBool::new(false);

    if !INITIALIZED.swap(true, core::sync::atomic::Ordering::SeqCst) {
        fbprintln!();
        fbprintln!("jarvis> ");
        kprintln!("[JARVIS Shell] Interactive shell ready");
    }

    // Check for keyboard input
    while let Some(byte) = crate::drivers::keyboard::read_byte() {
        if byte == b'\n' {
            // Process the command
            // For now, just echo back
            fbprintln!();
            fbprintln!("jarvis> ");
        }
    }
}

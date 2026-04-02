//! JARVIS Kernel Subsystems
//!
//! This is where JARVIS lives inside the kernel.
//! Each subsystem is a kernel task managed by the scheduler.

pub mod neural;
pub mod subsystems;

use crate::kprintln;
use crate::scheduler::{self, Priority, Task};

/// Initialize all JARVIS subsystems as kernel tasks
pub fn init() {
    kprintln!("[JARVIS] Booting JARVIS subsystems...");

    // Core subsystems — spawn as kernel tasks
    let mem_id = scheduler::spawn(Task::new(
        "jarvis.memory",
        Priority::Critical,
        subsystems::memory_tick,
    ));
    kprintln!("[JARVIS] Memory subsystem: task #{}", mem_id);

    let brain_id = scheduler::spawn(Task::new(
        "jarvis.brain",
        Priority::High,
        subsystems::brain_tick,
    ));
    kprintln!("[JARVIS] Brain subsystem: task #{}", brain_id);

    let speech_id = scheduler::spawn(Task::new(
        "jarvis.speech",
        Priority::Normal,
        subsystems::speech_tick,
    ));
    kprintln!("[JARVIS] Speech subsystem: task #{}", speech_id);

    let vision_id = scheduler::spawn(Task::new(
        "jarvis.vision",
        Priority::Normal,
        subsystems::vision_tick,
    ));
    kprintln!("[JARVIS] Vision subsystem: task #{}", vision_id);

    let evolution_id = scheduler::spawn(Task::new(
        "jarvis.evolution",
        Priority::Low,
        subsystems::evolution_tick,
    ));
    kprintln!("[JARVIS] Evolution subsystem: task #{}", evolution_id);

    let shell_id = scheduler::spawn(Task::new(
        "jarvis.shell",
        Priority::High,
        subsystems::shell_tick,
    ));
    kprintln!("[JARVIS] Shell subsystem: task #{}", shell_id);

    kprintln!(
        "[JARVIS] All subsystems online ({} tasks)",
        scheduler::task_count()
    );
}

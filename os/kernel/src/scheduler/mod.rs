//! JARVIS Task Scheduler
//!
//! Priority-based cooperative scheduler for kernel tasks.
//! Each JARVIS subsystem runs as a kernel task with its own
//! priority level and message queue.

use crate::kprintln;
use alloc::collections::VecDeque;
use alloc::string::String;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicU64, Ordering};
use spin::Mutex;

/// Task ID counter
static NEXT_TASK_ID: AtomicU64 = AtomicU64::new(1);

/// Task priority levels
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Priority {
    Critical = 0, // Memory, core kernel
    High = 1,     // Brain, reasoning
    Normal = 2,   // Web server, plugins
    Low = 3,      // Evolution, maintenance
    Idle = 4,     // Background tasks
}

/// Task state
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskState {
    Ready,
    Running,
    Blocked,
    Sleeping(u64), // Wake at this tick count
    Dead,
}

/// A kernel task
pub struct Task {
    pub id: u64,
    pub name: String,
    pub priority: Priority,
    pub state: TaskState,
    pub tick_fn: Option<fn()>, // Called each scheduler tick when task is Running
    pub messages: VecDeque<crate::ipc::Message>,
}

impl Task {
    pub fn new(name: &str, priority: Priority, tick_fn: fn()) -> Self {
        Task {
            id: NEXT_TASK_ID.fetch_add(1, Ordering::SeqCst),
            name: String::from(name),
            priority,
            state: TaskState::Ready,
            tick_fn: Some(tick_fn),
            messages: VecDeque::new(),
        }
    }
}

/// The scheduler
struct Scheduler {
    tasks: Vec<Task>,
    current_task: usize,
    tick_count: u64,
}

impl Scheduler {
    const fn new() -> Self {
        Scheduler {
            tasks: Vec::new(),
            current_task: 0,
            tick_count: 0,
        }
    }

    fn spawn(&mut self, task: Task) -> u64 {
        let id = task.id;
        kprintln!("[SCHED] Spawned task #{}: {} (priority: {:?})", id, task.name, task.priority);
        self.tasks.push(task);
        // Sort by priority (lower number = higher priority)
        self.tasks.sort_by_key(|t| t.priority);
        id
    }

    fn tick(&mut self) {
        self.tick_count += 1;

        // Wake sleeping tasks
        for task in self.tasks.iter_mut() {
            if let TaskState::Sleeping(wake_at) = task.state {
                if self.tick_count >= wake_at {
                    task.state = TaskState::Ready;
                }
            }
        }

        // Find next ready task (round-robin within same priority)
        let task_count = self.tasks.len();
        if task_count == 0 {
            return;
        }

        for i in 0..task_count {
            let idx = (self.current_task + i) % task_count;
            if self.tasks[idx].state == TaskState::Ready {
                self.tasks[idx].state = TaskState::Running;
                if let Some(tick_fn) = self.tasks[idx].tick_fn {
                    tick_fn();
                }
                self.tasks[idx].state = TaskState::Ready;
                self.current_task = (idx + 1) % task_count;
                return;
            }
        }
    }

    fn task_count(&self) -> usize {
        self.tasks.iter().filter(|t| t.state != TaskState::Dead).count()
    }

    fn send_message(&mut self, target_id: u64, msg: crate::ipc::Message) -> bool {
        for task in self.tasks.iter_mut() {
            if task.id == target_id {
                task.messages.push_back(msg);
                // Unblock if waiting for messages
                if task.state == TaskState::Blocked {
                    task.state = TaskState::Ready;
                }
                return true;
            }
        }
        false
    }
}

static SCHEDULER: Mutex<Scheduler> = Mutex::new(Scheduler::new());

/// Initialize the scheduler
pub fn init() {
    kprintln!("[SCHED] Scheduler initialized");
}

/// Spawn a new kernel task
pub fn spawn(task: Task) -> u64 {
    SCHEDULER.lock().spawn(task)
}

/// Called by the timer interrupt — drives task switching
pub fn on_tick() {
    // Only try to lock — if already locked (re-entrant), skip this tick
    if let Some(mut sched) = SCHEDULER.try_lock() {
        sched.tick();
    }
}

/// Get the number of active tasks
pub fn task_count() -> usize {
    SCHEDULER.lock().task_count()
}

/// Send a message to a task
pub fn send_to_task(target_id: u64, msg: crate::ipc::Message) -> bool {
    SCHEDULER.lock().send_message(target_id, msg)
}

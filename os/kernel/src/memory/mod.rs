//! JARVIS Memory Management
//!
//! Three-layer memory system:
//! 1. Frame Allocator — manages physical 4KB frames
//! 2. Page Tables — virtual → physical mapping
//! 3. Kernel Heap — alloc::* support for dynamic allocation

pub mod frame;
pub mod heap;
pub mod paging;

use crate::kprintln;
use bootloader_api::info::MemoryRegions;
use x86_64::VirtAddr;

/// Initialize the entire memory subsystem
pub fn init(physical_memory_offset: u64, memory_regions: &'static MemoryRegions) {
    let phys_offset = VirtAddr::new(physical_memory_offset);

    // Initialize frame allocator from bootloader memory map
    frame::init(memory_regions);
    kprintln!("[MEM] Frame allocator: {} usable frames", frame::usable_frame_count());

    // Initialize paging (we use the bootloader's existing page tables)
    paging::init(phys_offset);
    kprintln!("[MEM] Page tables initialized");

    // Initialize kernel heap
    heap::init();
    kprintln!("[MEM] Kernel heap: {}KB", heap::HEAP_SIZE / 1024);
}

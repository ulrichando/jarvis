//! Physical Frame Allocator
//!
//! Manages physical memory frames (4KB pages).
//! Uses a simple bitmap allocator — every bit represents one frame.

use bootloader_api::info::{MemoryRegionKind, MemoryRegions};
use spin::Mutex;
use x86_64::structures::paging::{FrameAllocator, PhysFrame, Size4KiB};
use x86_64::PhysAddr;

const MAX_FRAMES: usize = 1024 * 1024; // Support up to 4GB RAM (1M frames * 4KB)
const BITMAP_SIZE: usize = MAX_FRAMES / 8;

/// Bitmap frame allocator
struct BitmapAllocator {
    bitmap: [u8; BITMAP_SIZE],
    total_frames: usize,
    usable_frames: usize,
    next_free: usize,
}

impl BitmapAllocator {
    const fn new() -> Self {
        BitmapAllocator {
            bitmap: [0xFF; BITMAP_SIZE], // All frames marked as used initially
            total_frames: 0,
            usable_frames: 0,
            next_free: 0,
        }
    }

    fn mark_free(&mut self, frame_index: usize) {
        if frame_index < MAX_FRAMES {
            self.bitmap[frame_index / 8] &= !(1 << (frame_index % 8));
            self.usable_frames += 1;
        }
    }

    fn mark_used(&mut self, frame_index: usize) {
        if frame_index < MAX_FRAMES {
            self.bitmap[frame_index / 8] |= 1 << (frame_index % 8);
        }
    }

    fn is_free(&self, frame_index: usize) -> bool {
        if frame_index >= MAX_FRAMES {
            return false;
        }
        (self.bitmap[frame_index / 8] >> (frame_index % 8)) & 1 == 0
    }

    fn allocate(&mut self) -> Option<PhysFrame> {
        // Search from next_free for efficiency
        for i in 0..MAX_FRAMES {
            let idx = (self.next_free + i) % MAX_FRAMES;
            if self.is_free(idx) {
                self.mark_used(idx);
                self.next_free = (idx + 1) % MAX_FRAMES;
                let addr = PhysAddr::new((idx as u64) * 4096);
                return Some(PhysFrame::containing_address(addr));
            }
        }
        None
    }

    fn deallocate(&mut self, frame: PhysFrame) {
        let idx = frame.start_address().as_u64() as usize / 4096;
        self.mark_free(idx);
    }
}

static ALLOCATOR: Mutex<BitmapAllocator> = Mutex::new(BitmapAllocator::new());

/// Initialize the frame allocator from the bootloader's memory map
pub fn init(memory_regions: &'static MemoryRegions) {
    let mut alloc = ALLOCATOR.lock();

    for region in memory_regions.iter() {
        if region.kind == MemoryRegionKind::Usable {
            let start_frame = region.start as usize / 4096;
            let end_frame = region.end as usize / 4096;
            for frame_idx in start_frame..end_frame {
                alloc.mark_free(frame_idx);
                alloc.total_frames += 1;
            }
        }
    }
}

pub fn usable_frame_count() -> usize {
    ALLOCATOR.lock().usable_frames
}

/// Global frame allocator for use with x86_64 paging
pub struct JarvisFrameAllocator;

unsafe impl FrameAllocator<Size4KiB> for JarvisFrameAllocator {
    fn allocate_frame(&mut self) -> Option<PhysFrame<Size4KiB>> {
        ALLOCATOR.lock().allocate()
    }
}

impl JarvisFrameAllocator {
    pub fn deallocate_frame(&mut self, frame: PhysFrame) {
        ALLOCATOR.lock().deallocate(frame);
    }
}

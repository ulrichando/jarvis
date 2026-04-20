# JARVIS Kernel

A from-scratch x86_64 operating system kernel, written in Rust. No Linux, no
glibc, no init — this IS the OS, booting straight from the bootloader into our
own code.

**Status:** boots to a JARVIS banner in VGA text mode. Nothing else yet.

## Why

The MISTY desktop (at `src/os/desktop/`) is an Arch Linux rice — it's the
working JARVIS you can actually talk to every day. *This* directory is the
opposite extreme: a real kernel, written by hand, that eventually runs JARVIS
as its only user-space program. No distro, no package manager, just code
you wrote booting on bare metal (or QEMU).

## What exists right now

```
src/os/kernel/
├── Cargo.toml              # no_std, panic=abort, bootloader 0.9 + x86_64 crate
├── rust-toolchain.toml     # pins nightly for -Z build-std
├── x86_64-jarvis.json      # custom bare-metal target spec
├── .cargo/config.toml      # makes `cargo run` go through bootimage → qemu
├── src/
│   ├── main.rs             # kernel entry, clears VGA, draws JARVIS banner, halts
│   ├── vga.rs              # 80x25 VGA text-mode driver at 0xB8000
│   └── serial.rs           # COM1 0x3F8 driver for host debug via `-serial stdio`
└── scripts/run.sh          # one-liner to build + launch in QEMU
```

## Boot flow (today)

1. QEMU loads the BIOS.
2. BIOS loads our disk image; the `bootloader` crate's real-mode code runs.
3. Bootloader switches to long mode, sets up paging, passes a `BootInfo` struct.
4. Control transfers to our `kernel_main`.
5. We clear the VGA buffer and write the JARVIS banner.
6. `hlt` loop — there are no interrupts yet, so nothing else can happen.

## Run

```bash
# One-time setup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup override set nightly-2025-09-15   # or whatever the toolchain file pins
rustup component add rust-src llvm-tools-preview
cargo install bootimage
sudo pacman -S qemu-full                 # or: apt install qemu-system-x86

# Boot it
./scripts/run.sh
```

A QEMU window opens, shows the JARVIS banner in VGA text mode, then idles.
Close the window to exit.

## Roadmap

Order matters — each step depends on the previous one.

| # | Milestone | Why it matters | Rough LOC |
|---|---|---|---|
| 1 | **Interrupts** (IDT, GDT, PIC) | Nothing responds to the world without ISRs | ~400 |
| 2 | **Keyboard driver** | First input → first shell → first interaction | ~150 |
| 3 | **Heap allocator** | Rust collections, `String`, `Vec` start working | ~200 |
| 4 | **Paging / virtual memory** | Process isolation later depends on this | ~400 |
| 5 | **Serial shell** | Command-line via COM1: `help`, `mem`, `cpu`, `reboot` | ~300 |
| 6 | **Async executor** | No `std::thread`, but we can cooperatively run tasks | ~200 |
| 7 | **Framebuffer + font rasterizer** | Graphics. Goodbye 80x25 text. | ~600 |
| 8 | **VirtIO block driver + FAT32** | Persistent storage | ~800 |
| 9 | **VirtIO net driver + minimal TCP** | JARVIS can call Groq! | ~2000 |
| 10 | **Audio (AC97 or HDA)** | STT/TTS | ~700 |
| 11 | **Userspace + syscalls** | Run JARVIS as a separate process | ~1000 |
| 12 | **Minimal LLM client** | Native HTTP to Groq, no browser | ~400 |

Steps 1–6 are tractable over a few weekends. Everything past 7 is serious
work. The point isn't a finished OS — it's to *actually own the stack*.

## Design principles

- **No dependencies we can write ourselves.** Vendor-in small crates (volatile,
  spin, uart_16550) — avoid huge crates that pull std transitively.
- **JARVIS is the only application.** Once we have userspace, the kernel
  launches a single JARVIS process. No getty, no login, no tty multiplexing.
  Conversation is the shell.
- **Text first, graphics later.** VGA text mode + serial gets us to a
  feature-complete kernel faster than chasing pixels.
- **x86_64 only.** ARM/RISC-V ports are a separate repo, not a #[cfg] swamp.

## Relation to MISTY (Arch rice)

Completely independent. MISTY at `src/os/desktop/` is the daily driver and
will continue to be maintained. This kernel is a parallel research project;
nothing in `src/os/desktop/` depends on it and vice versa.

If this kernel ever catches up enough to run the JARVIS daemon natively,
we'll have a choice to make. Until then, MISTY is JARVIS's home.

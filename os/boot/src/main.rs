use std::path::PathBuf;

fn main() {
    let kernel_path = PathBuf::from(
        std::env::var("KERNEL_PATH").unwrap_or_else(|_| {
            // Default: look for the kernel binary relative to this project
            let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            let kernel_dir = here.parent().unwrap().join("kernel");

            let release = kernel_dir.join("target/x86_64-unknown-none/release/jarvis-kernel");
            if release.exists() {
                return release.to_string_lossy().to_string();
            }
            kernel_dir
                .join("target/x86_64-unknown-none/debug/jarvis-kernel")
                .to_string_lossy()
                .to_string()
        }),
    );

    println!("Kernel binary: {}", kernel_path.display());
    assert!(
        kernel_path.exists(),
        "Kernel binary not found at {}. Build it first with: cd os/kernel && cargo +nightly build --release",
        kernel_path.display()
    );

    // Create UEFI disk image
    let uefi_path = kernel_path.with_extension("uefi.img");
    println!("Creating UEFI image...");
    let uefi_builder = bootloader::UefiBoot::new(&kernel_path);
    uefi_builder
        .create_disk_image(&uefi_path)
        .expect("Failed to create UEFI disk image");
    println!("  UEFI: {}", uefi_path.display());

    // Create BIOS disk image
    let bios_path = kernel_path.with_extension("bios.img");
    println!("Creating BIOS image...");
    let bios_builder = bootloader::BiosBoot::new(&kernel_path);
    bios_builder
        .create_disk_image(&bios_path)
        .expect("Failed to create BIOS disk image");
    println!("  BIOS: {}", bios_path.display());

    println!();
    println!("╔══════════════════════════════════════════════╗");
    println!("║  JARVIS Kernel — Boot images ready!          ║");
    println!("╚══════════════════════════════════════════════╝");
    println!();
    println!("Test with QEMU:");
    println!("  qemu-system-x86_64 -drive format=raw,file={} -serial stdio", bios_path.display());
    println!();
    println!("Test with VirtualBox:");
    println!("  VBoxManage convertfromraw {} jarvis-kernel.vdi --format VDI", bios_path.display());
}

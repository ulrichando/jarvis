# ╔══════════════════════════════════════════════════════════════╗
# ║  JARVIS OS — Build System                                  ║
# ╚══════════════════════════════════════════════════════════════╝

.PHONY: build validate clean init status

# Default target
build: validate
	@echo "═══ Building JARVIS OS image... ═══"
	cd packer && packer build jarvis-os.pkr.hcl
	@echo ""
	@echo "═══ JARVIS OS build complete! ═══"
	@echo "Output: packer/output-jarvis-os/JARVIS-OS.ova"
	@echo ""
	@echo "Import into VirtualBox:"
	@echo "  VBoxManage import packer/output-jarvis-os/JARVIS-OS.ova"
	@echo ""
	@echo "Or: File → Import Appliance in VirtualBox GUI"

# Validate Packer template
validate:
	@echo "═══ Validating Packer template... ═══"
	cd packer && packer validate jarvis-os.pkr.hcl

# Initialize Packer plugins
init:
	@echo "═══ Initializing Packer plugins... ═══"
	cd packer && packer init jarvis-os.pkr.hcl

# Clean build artifacts
clean:
	@echo "═══ Cleaning build artifacts... ═══"
	rm -rf packer/output-jarvis-os
	@echo "Clean."

# Show service dependency graph
status:
	@echo ""
	@echo "  JARVIS OS Service Architecture"
	@echo "  ═══════════════════════════════"
	@echo ""
	@echo "  jarvis.target"
	@echo "    ├── jarvis-memory.service     (NeuralLattice)"
	@echo "    ├── jarvis-brain.service      (CogScript) → depends: memory"
	@echo "    ├── jarvis-grpc.service       (Rust gRPC)  → depends: brain"
	@echo "    ├── jarvis-web.service        (Web UI)     → depends: brain"
	@echo "    ├── jarvis-speech.service     (STT/TTS)    → depends: brain"
	@echo "    ├── jarvis-vision.service     (Camera/CV)  → depends: brain"
	@echo "    ├── jarvis-evolution.service  (Self-improve)→ depends: brain, memory"
	@echo "    ├── jarvis-plugins.service    (Plugins)    → depends: brain"
	@echo "    └── jarvis-greeting.service   (Boot voice) → depends: speech"
	@echo ""
	@echo "  Desktop: Sway → Chromium kiosk → localhost:8765"
	@echo "  Audio:   PipeWire → STT (Whisper) + TTS (edge-tts)"
	@echo ""

# Build with custom settings
build-custom:
	cd packer && packer build \
		-var 'cpus=2' \
		-var 'memory=2048' \
		-var 'disk_size=20000' \
		jarvis-os.pkr.hcl

# Quick test — just validate and show status
test: validate status
	@echo "All checks passed."

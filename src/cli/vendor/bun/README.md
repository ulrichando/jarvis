# Vendored Bun Runtime

Jarvis bundles Bun so production deployments do not depend on a machine-level Bun install.

Current bundled runtimes:
- Version: `1.3.12`
- Linux x64: [linux-x64/bun](/home/ulrich/Documents/Projects/jarvis/src/jarvis-cli/vendor/bun/linux-x64/bun)
- Windows x64: [windows-x64/bun.exe](/home/ulrich/Documents/Projects/jarvis/src/jarvis-cli/vendor/bun/windows-x64/bun.exe)

Resolution order in [scripts/bunw.sh](/home/ulrich/Documents/Projects/jarvis/src/jarvis-cli/scripts/bunw.sh):
- `BUN_BIN`
- `vendor/bun/<os>-<arch>/bun` or `bun.exe`
- `vendor/bun/bin/bun` or `bun.exe`
- `tools/bun/bin/bun`
- `PATH`
- standard install locations

Windows launchers:
- [scripts/start.ps1](/home/ulrich/Documents/Projects/jarvis/src/jarvis-cli/scripts/start.ps1)
- [scripts/start.cmd](/home/ulrich/Documents/Projects/jarvis/src/jarvis-cli/scripts/start.cmd)

To update the bundled runtime, replace the binary for the matching target and keep it executable.

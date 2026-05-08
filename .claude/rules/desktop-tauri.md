---
description: Desktop-Tauri release-build invariants and voice-UI constraints
paths:
  - src/desktop-tauri/**
---

# Desktop-Tauri rules

**`npm run build` alone does NOT ship JS changes.** Tauri embeds `dist/` into the Rust binary at compile time. Release flow:

```bash
cd src/desktop-tauri
npm run build           # rebuilds dist/
cargo build --release   # re-embeds dist/ into the binary
```

Skipping the second step ships the previous binary's dist/, and the user keeps seeing stale JS. Live failure history.

**Voice reactor sphere is intentionally removed.** Don't re-add per-frame React state to the voice UI — the sphere caused dropped audio frames. Static visualization only.

**Tauri dev:** `npm run tauri dev` (full hot reload).

**Misty Scone** ([src/os/desktop/](../../src/os/desktop/)) is a separate AI-native Arch rice that *copies* from cli/desktop-tauri. When making cross-cutting changes, treat them as two distinct apps — don't auto-propagate.

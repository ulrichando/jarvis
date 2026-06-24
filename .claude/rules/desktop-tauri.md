---
description: Desktop-Tauri release-build invariants and voice-UI constraints
paths:
  - src/voice-agent/desktop-tauri/**
---

# Desktop-Tauri rules

**`npm run build` alone does NOT ship JS changes.** Tauri embeds `dist/` into the Rust binary at compile time. Release flow:

```bash
cd src/voice-agent/desktop-tauri
npm run build           # rebuilds dist/
cargo build --release   # re-embeds dist/ into the binary
```

Skipping the second step ships the previous binary's dist/, and the user keeps seeing stale JS. Live failure history.

**Voice reactor sphere is intentionally removed.** Don't re-add per-frame React state to the voice UI — the sphere caused dropped audio frames. Static visualization only.

**The system-tray indicator is FROZEN (2026-05-20).** The two-axis indicator in `src-tauri/src/main.rs` — the 7 voice-state colours (`tray_image_for`), the magenta screen-share ring (`apply_sharing_ring`), the state set, the React→Rust poll rate, and `icons/tray.png` — is FINAL. Do NOT change colours, ring size/colour, poll rate, states, or the icon. The user was repeatedly frustrated by churn here (ring 3→5px, colour tweaks, poll 2→10 Hz across `030378c0` / `21ec58b6` / `e8cbdc31` / `702a1eb6`) and asked to lock it permanently. If a change ever seems warranted, **ASK FIRST** — don't just do it.

**Tauri dev:** `npm run tauri dev` (full hot reload).

**Misty Scone** ([src/os/desktop/](../../src/os/desktop/)) is a separate AI-native Arch rice that *copies* from cli/desktop-tauri. When making cross-cutting changes, treat them as two distinct apps — don't auto-propagate.

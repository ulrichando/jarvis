---
description: CLI codebase boundary rule (separate codebase — ask before editing src/cli/)
paths:
  - src/cli/**
---

# CLI rules — separate codebase, ask before modifying

`src/cli/` is the **`jarvis` CLI agent** (Claude-Code-shaped, TypeScript/Bun). It is a separate codebase from the voice-agent / desktop / web. When working on voice or desktop, **DO NOT edit src/cli/** — ask the user first.

**`src/cli/src/utils/jarvisInChrome/` is reserved** for future Firefox/Chrome extension work. Don't delete or refactor it as "unused." (Renamed from `claudeInChrome/` in the 2026-06-27 Claude→Jarvis rebrand; the internal MCP wire id stays `claude-in-chrome` for protocol stability.)

**Run:** `bin/jarvis` (top-level entrypoint, has gstack skill access).

**Build:** Bun-based; see [src/cli/package.json](../../src/cli/package.json).

The CLI has its own auto-memory layer separate from the voice-agent's memory dir. They don't share state.

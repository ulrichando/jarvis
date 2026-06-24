# News Widget — Design Spec

**Date:** 2026-04-29
**Status:** approved

## Overview

A customizable, always-visible desktop news widget built as a second Tauri window within the existing JARVIS desktop app. Renders RSS/Atom feed headlines in a small frameless transparent panel pinned to a corner of the desktop. Supports optional News API integration.

## Architecture

```
Second Tauri window ("news-widget") alongside the existing "main" overlay.
Frameless, transparent, ~400×600, pinned to user-chosen desktop corner.
Shares the React/Vite frontend stack with the main window.
```

### New Rust components

All new Rust code lives in `src-tauri/src/news.rs` (separate module, not inline in main.rs). main.rs registers the new commands and spawns the poll thread on setup.

- `init_news_module(app)` — called from main.rs setup, spawns poll thread, registers window

- `NewsConfig` — serde struct persisted to `~/.jarvis/news-widget.json`
- `NewsCache` — serde struct persisted to `~/.jarvis/news-cache.json`
- `fetch_feeds(config)` — fetches each feed URL via ureq, parses with `rss` crate, dedupes by title+link, writes cache
- `get_news` Tauri command — returns cached headlines to frontend, optionally triggers fetch if stale
- `update_config` / `get_config` Tauri commands — read/write the JSON config file
- Background poll thread — sleeps for config.refresh_interval, calls fetch_feeds, emits `news-updated` event to widget window

### New Web components

The widget gets its own HTML entry point: `src/voice-agent/desktop-tauri/news.html` (alongside existing `index.html`). Tauri loads it as a separate window with its own Vite build target. This keeps the widget bundle small and independent from the main overlay app.

Components live in `src/voice-agent/desktop-tauri/src/news/`:

- `NewsWidget` — main container, fetches headlines, listens for `news-updated`, renders in chosen layout
- `NewsSettings` — feed management, appearance, layout, refresh controls
- `NewsItem` — single headline card: title, source, timestamp, click → xdg-open
- Context menu (right-click) — refresh now, toggle settings, hide widget

## Data Flow

1. On startup, Rust loads config + cache from disk, spawns poll thread
2. Widget mounts → `invoke("get_news")` → gets cached headlines immediately
3. Poll thread fetches on interval → emits `news-updated` → widget re-renders
4. User changes settings → `invoke("update_config")` → Rust writes config, restarts poll thread with new interval
5. Clicking headline → `invoke("open_url", { url })` → Rust spawns `xdg-open`

## Configuration (customizable)

### Content
- Multiple RSS/Atom feed URLs with labels
- Optional News API key + topic selection
- Max headlines per feed / total
- Keyword filters (include/exclude)
- Refresh interval (30s to 24h)

### Appearance
- Theme: dark, light, transparent/glass
- Font size: small / medium / large
- Background opacity slider
- Accent color picker

### Layout
- List (vertical scroll of headline cards)
- Grid (2-column cards)
- Ticker (single-line horizontal scroll)

### Position
- Desktop corner: top-left, top-right, bottom-left, bottom-right
- Widget dimensions (width × height)
- Always-on-top toggle

## Error Handling

- Network errors → log warning, keep stale cache, show "last updated N min ago" badge
- Invalid RSS/XML → skip feed, don't crash poll
- Missing config → create defaults, show empty state with "add a feed" prompt
- All feeds fail → show "unable to refresh" with retry button

## Window Behavior

- Second Tauri window label: `news-widget`
- Frameless, transparent background
- Click-through on empty areas (headlines are clickable)
- Right-click context menu for quick actions
- Toggle visibility from system tray menu item
- Does NOT steal focus from other windows
- Position persists across restarts

## Tracker Menu Addition

New item in the system tray menu:
- "Show News Widget" toggle (checked/unchecked state)
- Opens/closes the news-widget window

## Rust Crates Needed

- `rss` — RSS/Atom feed parsing
- `ureq` — HTTP client (likely already in dependency tree)
- `serde` / `serde_json` — config and cache serialization (already used)

## Testing

- Rust: unit tests for RSS parsing with sample XML fixtures, config serialization round-trip, cache dedup logic
- Web: component renders with mock invoke responses, snapshot tests for list/grid/ticker layouts

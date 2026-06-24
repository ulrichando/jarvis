# News Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a customizable desktop news widget as a second Tauri window that renders RSS/Atom headlines in a frameless transparent overlay pinned to a desktop corner.

**Architecture:** New `news.rs` Rust module handles RSS fetching, caching, and config persistence. A second Tauri window (`news-widget`) loads its own Vite-built HTML page with React components for the widget UI, settings panel, and headline rendering. A background poll thread fetches on interval and emits events to the widget window.

**Tech Stack:** Rust (ureq, rss crate), Tauri 2, React 19, Vite 8, Tailwind CSS 4

---

### Task 1: Add Rust dependencies

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/Cargo.toml`

- [ ] **Step 1: Add rss and ureq crates**

Add under `[dependencies]`:
```toml
rss = "2"
ureq = "2"
```

- [ ] **Step 2: Build to verify dependencies resolve**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo check`
Expected: dependencies download and compile, no errors.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/Cargo.toml src/voice-agent/desktop-tauri/src-tauri/Cargo.lock
git commit -m "feat: add rss and ureq crates for news widget"
```

---

### Task 2: Create news.rs — Config and cache data structures

**Files:**
- Create: `src/voice-agent/desktop-tauri/src-tauri/src/news.rs`

- [ ] **Step 1: Write the module with structs and serialization**

```rust
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeedConfig {
    pub url: String,
    pub label: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppearanceConfig {
    pub theme: String,        // "dark", "light", "transparent"
    pub font_size: String,    // "small", "medium", "large"
    pub opacity: f32,         // 0.1 .. 1.0
    pub accent_color: String, // hex e.g. "#00e5ff"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsConfig {
    pub feeds: Vec<FeedConfig>,
    pub refresh_interval_secs: u64,
    pub max_headlines: usize,
    pub appearance: AppearanceConfig,
    pub layout: String,     // "list", "grid", "ticker"
    pub position: String,   // "top-left", "top-right", "bottom-left", "bottom-right"
    pub widget_width: u32,
    pub widget_height: u32,
    pub always_on_top: bool,
    pub news_api_key: Option<String>,
    pub keyword_include: Vec<String>,
    pub keyword_exclude: Vec<String>,
}

impl Default for NewsConfig {
    fn default() -> Self {
        Self {
            feeds: vec![],
            refresh_interval_secs: 300,
            max_headlines: 20,
            appearance: AppearanceConfig {
                theme: "transparent".into(),
                font_size: "medium".into(),
                opacity: 0.85,
                accent_color: "#00e5ff".into(),
            },
            layout: "list".into(),
            position: "top-right".into(),
            widget_width: 420,
            widget_height: 620,
            always_on_top: true,
            news_api_key: None,
            keyword_include: vec![],
            keyword_exclude: vec![],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsEntry {
    pub title: String,
    pub link: String,
    pub source_label: String,
    pub source_url: String,
    pub published: Option<String>,
    pub fetched_at: u64, // unix timestamp
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct NewsCache {
    pub entries: Vec<NewsEntry>,
    pub last_fetched_at: u64,
}

fn config_path() -> PathBuf {
    dirs().join("news-widget.json")
}

fn cache_path() -> PathBuf {
    dirs().join("news-cache.json")
}

fn dirs() -> PathBuf {
    std::env::var("HOME")
        .map(|h| PathBuf::from(h).join(".jarvis"))
        .unwrap_or_else(|_| PathBuf::from("."))
}
```

- [ ] **Step 2: Write failing test for default config**

Add at bottom of news.rs:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config_serialization_roundtrip() {
        let cfg = NewsConfig::default();
        let json = serde_json::to_string_pretty(&cfg).unwrap();
        let parsed: NewsConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.feeds.len(), 0);
        assert_eq!(parsed.refresh_interval_secs, 300);
        assert_eq!(parsed.layout, "list");
        assert_eq!(parsed.position, "top-right");
    }

    #[test]
    fn test_cache_dedup() {
        let mut cache = NewsCache::default();
        let e1 = NewsEntry {
            title: "Test".into(), link: "https://a.com/1".into(),
            source_label: "A".into(), source_url: "https://a.com".into(),
            published: None, fetched_at: 1000,
        };
        let e2 = NewsEntry {
            title: "Test".into(), link: "https://a.com/1".into(),
            source_label: "A".into(), source_url: "https://a.com".into(),
            published: None, fetched_at: 2000,
        };
        cache.entries.push(e1);
        // merge should not duplicate by link
        let merged = merge_entries(&cache.entries, &[e2]);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].fetched_at, 2000);
    }
}
```

- [ ] **Step 3: Run test to see it fail (merge_entries not defined yet)**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo test news::tests::test_cache_dedup`
Expected: compile error — `merge_entries` not found.

- [ ] **Step 4: Add `use` imports and implement merge_entries + load/save**

Add at top of news.rs (after existing use statements):
```rust
use std::fs;
use std::io;
```

Add after the `dirs()` function:
```rust
pub fn load_config() -> NewsConfig {
    let path = config_path();
    if path.exists() {
        fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    } else {
        let cfg = NewsConfig::default();
        let _ = save_config(&cfg);
        cfg
    }
}

pub fn save_config(cfg: &NewsConfig) -> io::Result<()> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&path, serde_json::to_string_pretty(cfg).unwrap())
}

pub fn load_cache() -> NewsCache {
    let path = cache_path();
    if path.exists() {
        fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    } else {
        NewsCache::default()
    }
}

pub fn save_cache(cache: &NewsCache) -> io::Result<()> {
    let path = cache_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&path, serde_json::to_string_pretty(cache).unwrap())
}

pub fn merge_entries(existing: &[NewsEntry], incoming: &[NewsEntry]) -> Vec<NewsEntry> {
    let mut merged: Vec<NewsEntry> = existing.to_vec();
    for entry in incoming {
        if let Some(pos) = merged.iter().position(|e| e.link == entry.link) {
            merged[pos] = entry.clone();
        } else {
            merged.push(entry.clone());
        }
    }
    merged.sort_by(|a, b| b.fetched_at.cmp(&a.fetched_at));
    merged
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo test news::tests`
Expected: both tests PASS.

- [ ] **Step 6: Run test for default config**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo test news::tests::test_default_config_serialization_roundtrip`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/news.rs
git commit -m "feat: add news config and cache data structures with tests"
```

---

### Task 3: Create news.rs — RSS fetching

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/news.rs`

- [ ] **Step 1: Add fetch_feeds function**

Add after `merge_entries` in news.rs:

```rust
use std::time::{SystemTime, UNIX_EPOCH};

pub fn fetch_feeds(config: &NewsConfig) -> Vec<NewsEntry> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut entries: Vec<NewsEntry> = Vec::new();

    for feed in &config.feeds {
        match fetch_and_parse_feed(&feed.url, &feed.label, now) {
            Ok(mut feed_entries) => entries.append(&mut feed_entries),
            Err(e) => eprintln!("[JARVIS] news: failed to fetch {}: {}", feed.url, e),
        }
    }

    // Apply keyword filters
    if !config.keyword_include.is_empty() {
        entries.retain(|e| {
            let lower = e.title.to_lowercase();
            config.keyword_include.iter().any(|kw| lower.contains(&kw.to_lowercase()))
        });
    }
    if !config.keyword_exclude.is_empty() {
        entries.retain(|e| {
            let lower = e.title.to_lowercase();
            !config.keyword_exclude.iter().any(|kw| lower.contains(&kw.to_lowercase()))
        });
    }

    entries.truncate(config.max_headlines);
    entries
}

fn fetch_and_parse_feed(url: &str, label: &str, now: u64) -> Result<Vec<NewsEntry>, String> {
    let response = ureq::get(url)
        .set("User-Agent", "JARVIS-News/0.1")
        .set("Accept", "application/rss+xml, application/atom+xml, application/xml, text/xml")
        .timeout(std::time::Duration::from_secs(15))
        .call()
        .map_err(|e| format!("HTTP error: {e}"))?;

    let body = response.into_string()
        .map_err(|e| format!("body read error: {e}"))?;

    let channel = rss::Channel::read_from(body.as_bytes())
        .map_err(|e| format!("RSS parse error: {e}"))?;

    let entries: Vec<NewsEntry> = channel.items().iter().map(|item| {
        NewsEntry {
            title: item.title().unwrap_or("(untitled)").to_string(),
            link: item.link().unwrap_or("#").to_string(),
            source_label: label.to_string(),
            source_url: url.to_string(),
            published: item.pub_date().map(|s| s.to_string()),
            fetched_at: now,
        }
    }).collect();

    Ok(entries)
}
```

- [ ] **Step 2: Add test with sample RSS XML**

Add to the tests module:
```rust
#[test]
fn test_parse_rss_from_bytes() {
    let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Headline One</title>
      <link>https://example.com/1</link>
      <pubDate>Mon, 28 Apr 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Headline Two</title>
      <link>https://example.com/2</link>
    </item>
  </channel>
</rss>"#;
    let channel = rss::Channel::read_from(xml.as_bytes()).unwrap();
    let items = channel.items();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0].title().unwrap(), "Headline One");
    assert_eq!(items[1].link().unwrap(), "https://example.com/2");
}

#[test]
fn test_keyword_filter() {
    let cfg = NewsConfig {
        keyword_include: vec!["tech".into()],
        keyword_exclude: vec!["crypto".into()],
        max_headlines: 10,
        ..Default::default()
    };
    let entries = vec![
        NewsEntry { title: "Tech news".into(), link: "1".into(), source_label: "A".into(), source_url: "a".into(), published: None, fetched_at: 1 },
        NewsEntry { title: "Sports news".into(), link: "2".into(), source_label: "A".into(), source_url: "a".into(), published: None, fetched_at: 1 },
        NewsEntry { title: "Tech crypto crash".into(), link: "3".into(), source_label: "B".into(), source_url: "b".into(), published: None, fetched_at: 1 },
    ];
    let mut filtered = entries.clone();
    filtered.retain(|e| {
        let lower = e.title.to_lowercase();
        if !cfg.keyword_include.is_empty() {
            if !cfg.keyword_include.iter().any(|kw| lower.contains(&kw.to_lowercase())) {
                return false;
            }
        }
        if !cfg.keyword_exclude.is_empty() {
            if cfg.keyword_exclude.iter().any(|kw| lower.contains(&kw.to_lowercase())) {
                return false;
            }
        }
        true
    });
    assert_eq!(filtered.len(), 1); // only "Tech news" — sports excluded by include, crypto excluded by exclude
    assert_eq!(filtered[0].title, "Tech news");
}
```

- [ ] **Step 3: Run tests**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo test news::tests`
Expected: all 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/news.rs
git commit -m "feat: add RSS feed fetching with keyword filtering"
```

---

### Task 4: Create news.rs — Tauri commands and poll thread

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/news.rs`

- [ ] **Step 1: Add Tauri command functions**

Add at the end of news.rs (before tests):

```rust
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager, State};
use std::thread;

pub struct NewsState {
    pub config: Arc<Mutex<NewsConfig>>,
    pub cache: Arc<Mutex<NewsCache>>,
}

#[tauri::command]
pub fn get_news(state: State<NewsState>) -> Result<Vec<NewsEntry>, String> {
    let cache = state.cache.lock().map_err(|e| e.to_string())?;
    Ok(cache.entries.clone())
}

#[tauri::command]
pub fn get_news_config(state: State<NewsState>) -> Result<NewsConfig, String> {
    let config = state.config.lock().map_err(|e| e.to_string())?;
    Ok(config.clone())
}

#[tauri::command]
pub fn update_news_config(
    new_config: NewsConfig,
    state: State<NewsState>,
) -> Result<(), String> {
    save_config(&new_config).map_err(|e| e.to_string())?;
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    *cfg = new_config;
    Ok(())
}

#[tauri::command]
pub fn refresh_news_now(
    state: State<NewsState>,
    app: AppHandle,
) -> Result<Vec<NewsEntry>, String> {
    let config = state.config.lock().map_err(|e| e.to_string())?;
    let new_entries = fetch_feeds(&config);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let mut cache = state.cache.lock().map_err(|e| e.to_string())?;
    let merged = merge_entries(&cache.entries, &new_entries);
    cache.entries = merged;
    cache.last_fetched_at = now;
    let _ = save_cache(&cache);

    // Emit event to widget window
    if let Some(window) = app.get_webview_window("news-widget") {
        let _ = window.emit("news-updated", &cache.entries);
    }

    Ok(cache.entries.clone())
}

#[tauri::command]
pub fn open_url(url: String) -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg(&url)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn spawn_poll_thread(app: AppHandle) {
    thread::spawn(move || {
        loop {
            // Read current config to get interval
            let interval = {
                let state: State<NewsState> = app.state();
                match state.config.lock() {
                    Ok(cfg) => cfg.refresh_interval_secs,
                    Err(_) => 300,
                }
            };

            thread::sleep(std::time::Duration::from_secs(interval));

            // Fetch and update
            let (entries, should_emit) = {
                let state: State<NewsState> = app.state();
                let config = match state.config.lock() {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                let new_entries = fetch_feeds(&config);
                let mut cache = match state.cache.lock() {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_secs();
                let merged = merge_entries(&cache.entries, &new_entries);
                let changed = merged.len() != cache.entries.len()
                    || merged.first().map(|e| e.link.clone()) != cache.entries.first().map(|e| e.link.clone());
                cache.entries = merged;
                cache.last_fetched_at = now;
                let _ = save_cache(&cache);
                (cache.entries.clone(), changed)
            };

            if should_emit {
                if let Some(window) = app.get_webview_window("news-widget") {
                    let _ = window.emit("news-updated", &entries);
                }
            }
        }
    });
}
```

- [ ] **Step 2: Run cargo check to verify compilation**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo check`
Expected: compiles successfully (will have warnings about unused functions until main.rs wires them).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/news.rs
git commit -m "feat: add Tauri commands and poll thread for news widget"
```

---

### Task 5: Wire news module into main.rs

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/main.rs`

- [ ] **Step 1: Add module declaration at top of main.rs**

Add after the existing `use` statements (around line 10):
```rust
mod news;
use news::{NewsState, load_config, load_cache, save_cache, merge_entries, fetch_feeds};
```

- [ ] **Step 2: Add news state initialization in setup**

In `main.rs`, inside the `.setup()` closure, after `let window = app.get_webview_window("main").unwrap();` (around line 512), add:

```rust
// ── News widget state ──
let news_config = news::load_config();
let news_cache = news::load_cache();
app.manage(NewsState {
    config: Arc::new(Mutex::new(news_config)),
    cache: Arc::new(Mutex::new(news_cache)),
});

// Spawn news poll thread
let news_app = app.handle().clone();
news::spawn_poll_thread(news_app);

// Open the news-widget window if configured feeds exist
{
    let state: tauri::State<NewsState> = app.state();
    if let Ok(cfg) = state.config.lock() {
        if !cfg.feeds.is_empty() {
            use tauri::Manager;
            if let Ok(()) = app.get_webview_window("news-widget").map(|w| {
                let _ = w.show();
            }) {
                // window exists in config, shown
            }
        }
    }
}
```

- [ ] **Step 3: Register news commands in invoke_handler**

In the `.invoke_handler()` call (around line 1086), add the news commands to the list:

Add these four entries to `tauri::generate_handler![]`:
```rust
news::get_news,
news::get_news_config,
news::update_news_config,
news::refresh_news_now,
news::open_url,
```

- [ ] **Step 4: Run cargo check to verify**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo check`
Expected: compiles successfully.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/main.rs
git commit -m "feat: wire news module into main.rs with commands and poll thread"
```

---

### Task 6: Add news-widget window to Tauri config

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json`

- [ ] **Step 1: Add news-widget window entry**

In `tauri.conf.json`, add a second window object inside the `"windows"` array:

```json
{
  "label": "news-widget",
  "title": "JARVIS News",
  "url": "news.html",
  "decorations": false,
  "transparent": true,
  "alwaysOnTop": true,
  "skipTaskbar": true,
  "resizable": true,
  "focus": false,
  "width": 420,
  "height": 620,
  "visible": false,
  "fullscreen": false
}
```

Note: place after the existing `"main"` window object (after its closing `}`), separated by a comma.

- [ ] **Step 2: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json
git commit -m "feat: add news-widget window config to tauri.conf.json"
```

---

### Task 7: Add multi-page Vite config and news HTML entry point

**Files:**
- Create: `src/voice-agent/desktop-tauri/news.html`
- Modify: `src/voice-agent/desktop-tauri/vite.config.js`

- [ ] **Step 1: Update vite.config.js for multi-page build**

Replace the `build` block in the existing config:
```js
build: {
  target: 'chrome105',
  minify: !process.env.TAURI_ENV_DEBUG ? 'esbuild' : false,
  sourcemap: !!process.env.TAURI_ENV_DEBUG,
  rollupOptions: {
    input: {
      main: 'index.html',
      news: 'news.html',
    },
  },
},
```

- [ ] **Step 2: Create news.html entry point**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>JARVIS News</title>
    <style>
      html, body, #root {
        margin: 0; padding: 0;
        background: transparent !important;
        color: #b0c8d4;
        overflow: hidden;
        width: 100vw; height: 100vh;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/news/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 3: Verify build**

Run: `cd src/voice-agent/desktop-tauri && npm run build`
Expected: build succeeds, `dist/news.html` and `dist/assets/news-*.js` exist.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/desktop-tauri/vite.config.js src/voice-agent/desktop-tauri/news.html
git commit -m "feat: add multi-page Vite config and news.html entry point"
```

---

### Task 8: Create news widget React components

**Files:**
- Create: `src/voice-agent/desktop-tauri/src/news/main.jsx`
- Create: `src/voice-agent/desktop-tauri/src/news/index.css`
- Create: `src/voice-agent/desktop-tauri/src/news/NewsWidget.jsx`
- Create: `src/voice-agent/desktop-tauri/src/news/NewsItem.jsx`
- Create: `src/voice-agent/desktop-tauri/src/news/NewsSettings.jsx`

- [ ] **Step 1: Create news/index.css**

```css
@import "tailwindcss";

.news-widget {
  font-family: 'Share Tech Mono', monospace;
  height: 100vh;
  overflow: hidden;
}

.news-scroll {
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 229, 255, 0.2) transparent;
}

.news-item {
  transition: background 0.15s ease;
}
.news-item:hover {
  background: rgba(0, 229, 255, 0.08);
}

/* Ticker animation */
@keyframes ticker-scroll {
  0% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}
.ticker-track {
  animation: ticker-scroll 30s linear infinite;
}
.ticker-track:hover {
  animation-play-state: paused;
}

/* Fade in for list items */
@keyframes news-fade-in {
  from { opacity: 0; transform: translateX(8px); }
  to { opacity: 1; transform: translateX(0); }
}
.news-fade-in {
  animation: news-fade-in 0.2s ease forwards;
}
```

- [ ] **Step 2: Create news/main.jsx**

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import NewsWidget from './NewsWidget.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <NewsWidget />
  </React.StrictMode>
)
```

- [ ] **Step 3: Create NewsItem.jsx**

```jsx
import { invoke } from '@tauri-apps/api/core'

export default function NewsItem({ entry, accentColor, index }) {
  const handleClick = () => {
    invoke('open_url', { url: entry.link })
  }

  const timeAgo = (() => {
    if (!entry.published) return ''
    try {
      const d = new Date(entry.published)
      const now = Date.now()
      const diff = Math.floor((now - d.getTime()) / 1000)
      if (diff < 60) return 'now'
      if (diff < 3600) return `${Math.floor(diff / 60)}m`
      if (diff < 86400) return `${Math.floor(diff / 3600)}h`
      return `${Math.floor(diff / 86400)}d`
    } catch { return '' }
  })()

  return (
    <div
      className="news-item news-fade-in px-3 py-2 cursor-pointer border-b"
      style={{
        borderColor: 'rgba(0, 229, 255, 0.08)',
        animationDelay: `${index * 30}ms`,
        opacity: 0,
      }}
      onClick={handleClick}
      onAnimationEnd={(e) => { e.currentTarget.style.opacity = 1 }}
    >
      <div className="text-sm leading-snug" style={{ color: '#d0dce4' }}>
        {entry.title}
      </div>
      <div className="flex items-center gap-2 mt-1 text-xs" style={{ color: 'rgba(176, 200, 212, 0.5)' }}>
        <span style={{ color: accentColor }}>{entry.source_label}</span>
        {timeAgo && <span>{timeAgo}</span>}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create NewsSettings.jsx**

```jsx
import { useState, useEffect } from 'react'
import { invoke } from '@tauri-apps/api/core'

const POSITIONS = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
const LAYOUTS = ['list', 'grid', 'ticker']
const THEMES = ['transparent', 'dark', 'light']
const FONT_SIZES = ['small', 'medium', 'large']

export default function NewsSettings({ config, onConfigChange, onClose }) {
  const [local, setLocal] = useState(config)
  const [newFeedUrl, setNewFeedUrl] = useState('')
  const [newFeedLabel, setNewFeedLabel] = useState('')

  useEffect(() => { setLocal(config) }, [config])

  const save = async () => {
    await invoke('update_news_config', { newConfig: local })
    onConfigChange(local)
  }

  const addFeed = () => {
    if (!newFeedUrl.trim()) return
    setLocal({
      ...local,
      feeds: [...local.feeds, { url: newFeedUrl.trim(), label: newFeedLabel.trim() || newFeedUrl.trim() }],
    })
    setNewFeedUrl('')
    setNewFeedLabel('')
  }

  const removeFeed = (idx) => {
    setLocal({ ...local, feeds: local.feeds.filter((_, i) => i !== idx) })
  }

  return (
    <div className="p-4 h-full overflow-y-auto news-scroll" style={{ background: 'rgba(2, 8, 16, 0.95)' }}>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-lg font-bold" style={{ color: '#00e5ff' }}>News Settings</h2>
        <button onClick={onClose} className="text-sm px-2 py-1 rounded" style={{ color: '#b0c8d4' }}>✕</button>
      </div>

      {/* Feeds */}
      <section className="mb-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: '#00e5ff' }}>Feeds</h3>
        <div className="flex gap-2 mb-2">
          <input
            className="flex-1 px-2 py-1 rounded text-sm bg-black/30 border"
            style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            placeholder="Feed URL"
            value={newFeedUrl}
            onChange={(e) => setNewFeedUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addFeed()}
          />
          <input
            className="w-24 px-2 py-1 rounded text-sm bg-black/30 border"
            style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            placeholder="Label"
            value={newFeedLabel}
            onChange={(e) => setNewFeedLabel(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addFeed()}
          />
          <button onClick={addFeed} className="px-3 py-1 rounded text-sm font-bold" style={{ background: 'rgba(0,229,255,0.15)', color: '#00e5ff' }}>
            +
          </button>
        </div>
        <div className="space-y-1 max-h-32 overflow-y-auto news-scroll">
          {local.feeds.map((feed, i) => (
            <div key={i} className="flex justify-between items-center text-xs px-2 py-1 rounded" style={{ background: 'rgba(0,229,255,0.05)' }}>
              <span className="truncate flex-1" style={{ color: '#b0c8d4' }}>{feed.label || feed.url}</span>
              <button onClick={() => removeFeed(i)} className="ml-2" style={{ color: '#ff3333' }}>✕</button>
            </div>
          ))}
        </div>
      </section>

      {/* Appearance */}
      <section className="mb-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: '#00e5ff' }}>Appearance</h3>

        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Theme
          <select className="block w-full mt-1 px-2 py-1 rounded text-sm bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            value={local.appearance.theme}
            onChange={(e) => setLocal({ ...local, appearance: { ...local.appearance, theme: e.target.value } })}>
            {THEMES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>

        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Font size
          <select className="block w-full mt-1 px-2 py-1 rounded text-sm bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            value={local.appearance.font_size}
            onChange={(e) => setLocal({ ...local, appearance: { ...local.appearance, font_size: e.target.value } })}>
            {FONT_SIZES.map(fs => <option key={fs} value={fs}>{fs}</option>)}
          </select>
        </label>

        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Opacity: {local.appearance.opacity.toFixed(1)}
          <input type="range" min="0.1" max="1.0" step="0.05" className="block w-full mt-1"
            value={local.appearance.opacity}
            onChange={(e) => setLocal({ ...local, appearance: { ...local.appearance, opacity: parseFloat(e.target.value) } })} />
        </label>

        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Accent color
          <input type="color" className="block w-full mt-1 h-8 rounded bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)' }}
            value={local.appearance.accent_color}
            onChange={(e) => setLocal({ ...local, appearance: { ...local.appearance, accent_color: e.target.value } })} />
        </label>
      </section>

      {/* Layout */}
      <section className="mb-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: '#00e5ff' }}>Layout</h3>
        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Style
          <select className="block w-full mt-1 px-2 py-1 rounded text-sm bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            value={local.layout}
            onChange={(e) => setLocal({ ...local, layout: e.target.value })}>
            {LAYOUTS.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Position
          <select className="block w-full mt-1 px-2 py-1 rounded text-sm bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            value={local.position}
            onChange={(e) => setLocal({ ...local, position: e.target.value })}>
            {POSITIONS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </section>

      {/* Refresh */}
      <section className="mb-4">
        <h3 className="text-sm font-semibold mb-2" style={{ color: '#00e5ff' }}>Refresh</h3>
        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Interval: {local.refresh_interval_secs}s
          <select className="block w-full mt-1 px-2 py-1 rounded text-sm bg-black/30 border" style={{ borderColor: 'rgba(0,229,255,0.15)', color: '#d0dce4' }}
            value={local.refresh_interval_secs}
            onChange={(e) => setLocal({ ...local, refresh_interval_secs: parseInt(e.target.value) })}>
            <option value="30">30s</option>
            <option value="60">1m</option>
            <option value="300">5m</option>
            <option value="600">10m</option>
            <option value="1800">30m</option>
            <option value="3600">1h</option>
            <option value="86400">24h</option>
          </select>
        </label>
        <label className="block text-xs mb-2" style={{ color: '#b0c8d4' }}>
          Max headlines: {local.max_headlines}
          <input type="range" min="5" max="100" step="5" className="block w-full mt-1"
            value={local.max_headlines}
            onChange={(e) => setLocal({ ...local, max_headlines: parseInt(e.target.value) })} />
        </label>
      </section>

      {/* Save */}
      <button onClick={save} className="w-full py-2 rounded font-bold text-sm" style={{ background: 'rgba(0,229,255,0.2)', color: '#00e5ff' }}>
        Save Settings
      </button>
    </div>
  )
}
```

- [ ] **Step 5: Create NewsWidget.jsx**

```jsx
import { useState, useEffect, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import NewsItem from './NewsItem.jsx'
import NewsSettings from './NewsSettings.jsx'

export default function NewsWidget() {
  const [entries, setEntries] = useState([])
  const [config, setConfig] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [lastFetched, setLastFetched] = useState(null)

  useEffect(() => {
    // Load config and cached entries on mount
    (async () => {
      const cfg = await invoke('get_news_config')
      setConfig(cfg)
      const items = await invoke('get_news')
      setEntries(items)
      if (items.length > 0) {
        setLastFetched(new Date())
      }
    })()

    // Listen for poll thread updates
    const unlisten = listen('news-updated', (event) => {
      setEntries(event.payload)
      setLastFetched(new Date())
    })

    return () => { unlisten.then(fn => fn()) }
  }, [])

  // Right-click context menu
  const handleContextMenu = useCallback((e) => {
    e.preventDefault()
    const menu = document.createElement('div')
    menu.style.cssText = `
      position: fixed; left: ${e.clientX}px; top: ${e.clientY}px;
      background: rgba(2,8,16,0.95); border: 1px solid rgba(0,229,255,0.15);
      border-radius: 8px; padding: 4px; z-index: 9999; min-width: 160px;
    `
    const items = [
      { label: 'Refresh now', action: async () => {
        const fresh = await invoke('refresh_news_now')
        setEntries(fresh)
        setLastFetched(new Date())
      }},
      { label: showSettings ? 'Back to headlines' : 'Settings', action: () => setShowSettings(!showSettings) },
      { label: 'Close widget', action: async () => {
        // Toggle tray show/hide state — just hide for now
        document.getElementById('root').style.display = 'none'
      }},
    ]
    items.forEach(it => {
      const btn = document.createElement('button')
      btn.textContent = it.label
      btn.style.cssText = 'display:block;width:100%;padding:6px 10px;text-align:left;background:none;border:none;color:#b0c8d4;cursor:pointer;font:12px monospace;border-radius:4px;'
      btn.onmouseenter = () => btn.style.background = 'rgba(0,229,255,0.1)'
      btn.onmouseleave = () => btn.style.background = 'none'
      btn.onclick = () => { it.action(); menu.remove() }
      menu.appendChild(btn)
    })
    document.body.appendChild(menu)
    const close = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', close) } }
    setTimeout(() => document.addEventListener('click', close), 0)
  }, [showSettings])

  if (!config) {
    return (
      <div className="news-widget flex items-center justify-center h-full" style={{ background: `rgba(2,8,16,${0.85})` }}>
        <p className="text-sm" style={{ color: '#b0c8d4' }}>Loading...</p>
      </div>
    )
  }

  if (showSettings) {
    return (
      <div className="news-widget" onContextMenu={handleContextMenu}
        style={{ background: `rgba(2,8,16,${config.appearance.opacity})` }}>
        <NewsSettings config={config} onConfigChange={setConfig} onClose={() => setShowSettings(false)} />
      </div>
    )
  }

  const accent = config.appearance.accent_color
  const bg = config.appearance.theme === 'dark' ? `rgba(2,8,16,${config.appearance.opacity})`
    : config.appearance.theme === 'light' ? `rgba(240,244,248,${config.appearance.opacity})`
    : `rgba(2,8,16,${config.appearance.opacity * 0.6})`

  const fontSize = config.appearance.font_size === 'small' ? 'text-xs'
    : config.appearance.font_size === 'large' ? 'text-base' : 'text-sm'

  return (
    <div className={`news-widget ${fontSize}`} onContextMenu={handleContextMenu} style={{ background: bg }}>
      {/* Header */}
      <div className="flex justify-between items-center px-3 py-2 border-b" style={{ borderColor: 'rgba(0,229,255,0.1)' }}>
        <span className="font-bold text-sm" style={{ color: accent }}>JARVIS News</span>
        <div className="flex items-center gap-2">
          <button onClick={() => invoke('refresh_news_now').then(setEntries)} className="text-xs px-2 py-0.5 rounded"
            style={{ background: 'rgba(0,229,255,0.1)', color: accent }} title="Refresh">
            ↻
          </button>
          <button onClick={() => setShowSettings(true)} className="text-xs px-2 py-0.5 rounded"
            style={{ background: 'rgba(0,229,255,0.1)', color: accent }} title="Settings">
            ⚙
          </button>
        </div>
      </div>

      {/* Entries */}
      {config.layout === 'ticker' ? (
        <div className="overflow-hidden py-2" style={{ height: 'calc(100% - 40px)' }}>
          <div className="ticker-track flex gap-6 whitespace-nowrap" style={{ width: 'max-content' }}>
            {entries.map((entry, i) => (
              <span key={i} className="inline-flex items-center gap-2 cursor-pointer px-3"
                onClick={() => invoke('open_url', { url: entry.link })}
                style={{ color: '#d0dce4' }}>
                <span style={{ color: accent }}>{entry.source_label}</span>
                {entry.title}
              </span>
            ))}
          </div>
        </div>
      ) : config.layout === 'grid' ? (
        <div className="grid grid-cols-2 gap-1 p-1 overflow-y-auto news-scroll" style={{ height: 'calc(100% - 40px)' }}>
          {entries.map((entry, i) => (
            <div key={i} className="news-item news-fade-in p-2 cursor-pointer rounded"
              style={{ borderColor: 'rgba(0,229,255,0.08)', animationDelay: `${i * 20}ms`, opacity: 0 }}
              onClick={() => invoke('open_url', { url: entry.link })}
              onAnimationEnd={(e) => { e.currentTarget.style.opacity = 1 }}>
              <div className="text-xs leading-snug line-clamp-3" style={{ color: '#d0dce4' }}>{entry.title}</div>
              <div className="text-xs mt-1" style={{ color: accent }}>{entry.source_label}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="overflow-y-auto news-scroll" style={{ height: 'calc(100% - 40px)' }}>
          {entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <p className="text-sm" style={{ color: 'rgba(176,200,212,0.5)' }}>No headlines yet</p>
              <p className="text-xs" style={{ color: 'rgba(176,200,212,0.35)' }}>Right-click → Settings to add feeds</p>
            </div>
          ) : (
            entries.map((entry, i) => (
              <NewsItem key={entry.link} entry={entry} accentColor={accent} index={i} />
            ))
          )}
        </div>
      )}

      {/* Footer */}
      {lastFetched && (
        <div className="px-3 py-1 border-t text-xs" style={{ borderColor: 'rgba(0,229,255,0.08)', color: 'rgba(176,200,212,0.35)' }}>
          Updated {lastFetched.toLocaleTimeString()}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Verify build compiles the news bundle**

Run: `cd src/voice-agent/desktop-tauri && npm run build`
Expected: build succeeds, `dist/assets/news-*.js` exists alongside `dist/assets/main-*.js`.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/desktop-tauri/src/news/
git commit -m "feat: add news widget React components (list, grid, ticker layouts)"
```

---

### Task 9: Add tray menu toggle and window positioning

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/main.rs`

- [ ] **Step 1: Add tray menu item for news widget toggle**

In main.rs, in the menu builder section (after the `// ── System tray ──` comment), add a new menu item alongside the existing ones. Insert after `stop_cu_item`:

```rust
let news_item = MenuItemBuilder::with_id("news_toggle", "Show News Widget").build(app)?;
```

And add the separator and news item to the menu builder. After `.item(&stop_cu_item)`, add:

```rust
.item(&news_item)
```

- [ ] **Step 2: Add news_toggle handler in on_menu_event**

Inside the `on_menu_event` closure, add a new match arm alongside the existing ones:

```rust
"news_toggle" => {
    if let Some(w) = app.get_webview_window("news-widget") {
        match w.is_visible() {
            Ok(true) => {
                let _ = w.hide();
                // Update menu item text?
            }
            Ok(false) => {
                // Position the window
                let state: tauri::State<NewsState> = app.state();
                if let Ok(cfg) = state.config.lock() {
                    let (mx, my) = {
                        if let Ok(Some(m)) = w.primary_monitor() {
                            let s = m.size();
                            let p = m.position();
                            let x = match cfg.position.as_str() {
                                "top-right" | "bottom-right" => p.x + (s.width as i32 - cfg.widget_width as i32 - 20),
                                _ => p.x + 20,
                            };
                            let y = match cfg.position.as_str() {
                                "bottom-left" | "bottom-right" => p.y + (s.height as i32 - cfg.widget_height as i32 - 40),
                                _ => p.y + 40,
                            };
                            (x, y)
                        } else {
                            (100, 100)
                        }
                    };
                    use tauri::PhysicalPosition;
                    use tauri::PhysicalSize;
                    let _ = w.set_size(PhysicalSize::new(cfg.widget_width, cfg.widget_height));
                    let _ = w.set_position(PhysicalPosition::new(mx, my));
                    let _ = w.set_always_on_top(cfg.always_on_top);
                }
                let _ = w.show();
                let _ = w.set_focus();
            }
            _ => {}
        }
    }
}
```

- [ ] **Step 3: Verify compilation**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo check`
Expected: compiles successfully.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/main.rs
git commit -m "feat: add tray menu toggle for news widget with window positioning"
```

---

### Task 10: Final integration test and build

**Files:**
- No new files — verification only

- [ ] **Step 1: Run full cargo check + tests**

Run: `cd src/voice-agent/desktop-tauri/src-tauri && cargo test`
Expected: all Rust tests PASS.

- [ ] **Step 2: Run full frontend build**

Run: `cd src/voice-agent/desktop-tauri && npm run build`
Expected: build succeeds, `dist/` contains `index.html`, `news.html`, and asset bundles for both.

- [ ] **Step 3: Final commit (if any changes)**

```bash
git add -A
git diff --cached --stat
# Only commit if there are remaining changes
git commit -m "chore: final integration verification" --allow-empty
```

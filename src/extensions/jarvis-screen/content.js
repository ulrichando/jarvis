// JARVIS content script — DOM extraction (read-only)
// Injected into every tab at document_idle

;(function () {
  'use strict'

  // ── Page type detection ──────────────────────────────────────────────────

  function detectPageType() {
    const ogType = document.querySelector('meta[property="og:type"]')?.content || ''
    if (ogType.includes('article') || ogType.includes('news')) return 'article'
    if (ogType.includes('product')) return 'shop'
    if (ogType.includes('video')) return 'video'

    const url = location.href
    if (/[?&]q=|[?&]query=|\/search[?/]/.test(url)) return 'search'
    if (/\/product\/|\/item\/|\/p\/[0-9]/.test(url)) return 'shop'
    if (/youtube\.com\/watch|vimeo\.com\/[0-9]/.test(url)) return 'video'
    if (/\/docs\/|\/documentation\/|\/api\/|\/reference\//.test(url)) return 'docs'

    if (document.querySelector('article')) return 'article'
    if (document.querySelector('[itemtype*="Product"]')) return 'shop'

    return 'general'
  }

  // ── DOM extraction ───────────────────────────────────────────────────────

  function extractDOM() {
    try {
      // Clone body so we don't mutate the live page
      const clone = document.body.cloneNode(true)

      // Remove noise
      const noisy = [
        'script', 'style', 'noscript', 'iframe',
        'nav', 'footer', 'header', 'aside',
        '[role="banner"]', '[role="navigation"]', '[role="complementary"]',
        '[aria-hidden="true"]',
      ]
      noisy.forEach(sel => {
        try { clone.querySelectorAll(sel).forEach(el => el.remove()) } catch {}
      })
      // Also remove common ad/cookie class patterns
      ;['ad', 'ads', 'advertisement', 'cookie', 'popup', 'modal', 'overlay',
        'banner', 'promo', 'newsletter'].forEach(cls => {
        try {
          clone.querySelectorAll(
            `[class*="${cls}"], [id*="${cls}"]`
          ).forEach(el => el.remove())
        } catch {}
      })

      // Extract clean text
      const raw = clone.innerText || clone.textContent || ''
      const lines = raw.split('\n')
        .map(l => l.trim())
        .filter(l => l.length > 0)
      // Deduplicate consecutive identical lines (nav repetition)
      const deduped = lines.filter((l, i) => l !== lines[i - 1])
      const text = deduped.join('\n').slice(0, 8000)

      // Headings (from original DOM)
      const headings = []
      document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
        const t = h.textContent.trim()
        if (t && headings.length < 30) {
          headings.push({ level: parseInt(h.tagName[1]), text: t.slice(0, 120) })
        }
      })

      // Meta
      const description =
        document.querySelector('meta[name="description"]')?.content ||
        document.querySelector('meta[property="og:description"]')?.content ||
        ''

      return {
        url: location.href,
        title: document.title,
        description: description.slice(0, 300),
        lang: document.documentElement.lang || '',
        pageType: detectPageType(),
        headings,
        text,
        wordCount: text.split(/\s+/).filter(Boolean).length,
      }
    } catch (e) {
      return {
        url: location.href,
        title: document.title,
        description: '',
        lang: '',
        pageType: 'general',
        headings: [],
        text: '',
        wordCount: 0,
        error: e.message,
      }
    }
  }

  // ── Message listener ─────────────────────────────────────────────────────
  //
  // Two dispatch paths:
  //   1. `extract-dom` — internal side-panel observer, returns the
  //      heuristic page summary defined above.
  //   2. Any other action — bridge → background.js _forwardToContent →
  //      here. Map verb to ext_<verb> handler in __jarvisActions
  //      (set up by actions.js, loaded just before this file). This
  //      wiring is what makes click / type / scroll / dom_summary /
  //      get_url etc. actually do something — without it those tools
  //      time out silently and the LLM hallucinates success.

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || !msg.action) {
      sendResponse({ ok: false, error: 'no action' })
      return false
    }
    if (msg.action === 'extract-dom') {
      sendResponse(extractDOM())
      return false  // synchronous response
    }
    const actions = (typeof globalThis !== 'undefined') && globalThis.__jarvisActions
    if (!actions) {
      sendResponse({ ok: false, error: 'actions registry missing — actions.js failed to load' })
      return false
    }
    const fn = actions['ext_' + msg.action]
    if (typeof fn !== 'function') {
      sendResponse({ ok: false, error: 'unknown content action: ' + msg.action })
      return false
    }
    try {
      const result = fn(msg.args || {})
      if (result && typeof result.then === 'function') {
        // Async handler (e.g. ext_wait_for) — keep the message channel
        // open by returning true.
        result.then(r => sendResponse(r))
              .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }))
        return true
      }
      sendResponse(result)
      return false
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) })
      return false
    }
  })

})()

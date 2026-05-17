// Shared element-ranking heuristic for the browser subagent's `observe`
// tool. Mirrors Stagehand's observe() + browser-use's find_elements
// patterns — returns a ranked array of actionable elements with stable
// selectors so the supervisor LLM can pick deterministically.
//
// Pure heuristic — no extra LLM call. Saves tokens because the LLM
// doesn't have to scan the full DOM each turn; it queries by intent
// ("submit button", "search box") and gets back ≤N candidates.
//
// This file is read at import time by:
//   - src/voice-agent/tools/browser_cdp.py — for Playwright `page.evaluate`
//
// The Chrome extension's background.js has an inline copy in `_bgObserve`
// that should stay in sync with this file until the extension is
// refactored to load this file too (TODO). Update both when the scoring
// rules change.
//
// Returns: { matches: [{selector, tag, role, text, suggested_method, score}],
//            count: number, query: string }

(function(q, lim) {
    const lower = (q || "").toLowerCase().trim();
    const candidates = [];

    // Tag-level semantic weight — favor explicit interactive tags.
    const TAG_WEIGHT = {
        button: 1.0, a: 0.95, input: 0.9, select: 0.85, textarea: 0.85,
        summary: 0.7, label: 0.6, li: 0.4, span: 0.3, div: 0.2,
    };
    const ROLE_WEIGHT = {
        button: 1.0, link: 0.95, textbox: 0.9, combobox: 0.85,
        checkbox: 0.8, menuitem: 0.7, tab: 0.7, switch: 0.7,
    };

    const sel = 'a,button,input,textarea,select,summary,label,'
        + '[role="button"],[role="link"],[role="textbox"],[role="combobox"],'
        + '[role="checkbox"],[role="menuitem"],[role="tab"],[role="switch"],'
        + '[onclick],[contenteditable="true"]';

    const all = document.querySelectorAll(sel);
    for (const el of all) {
        // Visibility: skip hidden / zero-size.
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        if (parseFloat(cs.opacity || '1') < 0.05) continue;
        // Skip disabled.
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
        // Score.
        const tag = el.tagName.toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        const text = (
            el.innerText || el.textContent ||
            el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
            el.getAttribute('title') || el.getAttribute('value') || ''
        ).trim().toLowerCase();
        let score = (TAG_WEIGHT[tag] || 0.3) + (ROLE_WEIGHT[role] || 0);
        if (lower) {
            if (text === lower)               score += 3.0;
            else if (text.startsWith(lower))  score += 2.0;
            else if (text.includes(lower))    score += 1.5;
            else {
                // Word-level match.
                const words = lower.split(/\s+/);
                const matched = words.filter(w => w && text.includes(w));
                if (matched.length) score += 0.6 * (matched.length / words.length);
                else continue;  // skip — no relevance to query
            }
        }
        candidates.push({ el, tag, role, text, score, rect: r });
    }

    // Top-N by score.
    candidates.sort((a, b) => b.score - a.score);
    const top = candidates.slice(0, Math.max(1, Math.min(lim, 20)));

    // Build a stable selector for each: prefer #id, then aria-label,
    // then unique attribute, then tag+nth-of-type fallback.
    function selectorFor(el) {
        if (el.id) {
            const escaped = (typeof CSS !== 'undefined' && CSS.escape)
                ? CSS.escape(el.id) : el.id.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
            return `#${escaped}`;
        }
        const aria = el.getAttribute('aria-label');
        if (aria) {
            return `[aria-label="${aria.replace(/"/g, '\\"')}"]`;
        }
        const name = el.getAttribute('name');
        if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
        const dt = el.getAttribute('data-testid');
        if (dt) return `[data-testid="${dt}"]`;
        // Fallback: tag + nth-of-type within parent.
        const parent = el.parentElement;
        if (parent) {
            const siblings = Array.from(parent.children).filter(
                c => c.tagName === el.tagName
            );
            const idx = siblings.indexOf(el) + 1;
            return `${el.tagName.toLowerCase()}:nth-of-type(${idx})`;
        }
        return el.tagName.toLowerCase();
    }

    function suggestMethod(tag, role, el) {
        if (tag === 'input') {
            const t = (el.type || '').toLowerCase();
            if (['checkbox', 'radio', 'submit', 'button'].includes(t)) return 'click';
            return 'type';
        }
        if (tag === 'textarea' || el.contentEditable === 'true') return 'type';
        if (tag === 'select') return 'select';
        return 'click';
    }

    return {
        matches: top.map(c => ({
            selector: selectorFor(c.el),
            tag: c.tag,
            role: c.role || null,
            text: c.text.slice(0, 120),
            suggested_method: suggestMethod(c.tag, c.role, c.el),
            score: Math.round(c.score * 100) / 100,
        })),
        count: top.length,
        query: q,
    };
})

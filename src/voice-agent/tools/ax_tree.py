"""Accessibility-tree extractor via Chrome DevTools Protocol (CDP).

**Status: design sketch, not implemented.**

This module is a placeholder for the AX-tree feature that would give JARVIS
per-element access to web page content rendered in Chrome/Chromium. It is NOT
imported or wired anywhere.

Why an AX tree?
---------------
The current ``wmctrl`` window list provides only *window-level* elements
(titles + bounds). SOM overlays at the window level are useful for clicking
between windows, but they can't target actual UI elements *within* a page
(buttons, links, input fields). Hermes Agent's primary advantage is the
accessibility tree — per-control element info with ref IDs.

How it would work
-----------------
1. Find a running Chrome/Chromium instance with remote debugging enabled.
   JARVIS typically has Chrome open. If launched with
   ``--remote-debugging-port=9222``, Chrome exposes a CDP WebSocket at
   ``ws://127.0.0.1:9222/devtools/browser/<id>``.

2. Connect to the CDP endpoint using ``websockets`` or Python's ``asyncio``,
   discover available targets (tabs), and attach to the active/visible one.

3. Call ``Accessibility.getFullAXTree`` — returns the full accessibility tree
   with node IDs, roles, names, bounds, and relationships.

4. Map AX nodes to their on-screen bounding boxes. Chrome returns most
   interactive nodes with ``backendNodeId`` and bounding boxes that can be
   resolved via ``DOM.getBoxModel``.

5. Return a ``UIElement[]`` list that the existing SOM overlay renderer
   can use. Each element gets a 1-based index for click targeting.

Challenges
----------
1. **No guarantee Chrome runs with ``--remote-debugging-port``.** The standard
   Playwright/browser_use launches use a dedicated profile, not the user's
   daily Chrome. The user's own Chrome may or may not have CDP enabled.

2. **AX tree ≠ screen.** The AX tree covers the active tab, not the full
   desktop — so it can't replace the ``wmctrl`` window list. It's
   complementary: merge AX elements from the focused Chrome tab with
   wmctrl window-level elements for other apps.

3. **AX tree is large.** A complex page (Gmail, Google Docs) can have
   thousands of AX nodes. Need aggressive filtering (interactive-only,
   visible-only) to keep token cost manageable.

4. **Coordinate mapping.** AX node bounds are in viewport coordinates,
   not screen coordinates. Need to account for browser chrome (title bar,
   tabs, toolbar) and window position to map to absolute screen coords.

5. **Permission.** Accessing the user's browser tabs via CDP means reading
   all page content. This is a capability escalation that should be
   explicitly gated.

6. **Dependency.** Would add ``websockets`` or similar to the voice venv.
   Currently stdlib-only.

Implementation sketch
---------------------
```python
import asyncio
import json
from typing import Optional

class AXTreeExtractor:
    """Extract accessibility tree from Chrome via CDP."""

    def __init__(self, cdp_url: str = "ws://127.0.0.1:9222"):
        self._cdp_url = cdp_url
        self._ws = None

    async def connect(self) -> bool:
        # 1. GET /json/version to verify Chrome is listening
        # 2. GET /json to list targets (tabs)
        # 3. Find active/visible tab
        # 4. Connect via WebSocket
        ...

    async def extract_ax_tree(self) -> list[dict]:
        # 1. Send Accessibility.getFullAXTree
        # 2. Filter: interactive nodes only (button, link, input, etc.)
        # 3. Map backendNodeIds to DOM.getBoxModel for bounds
        # 4. Return list of {"id", "role", "name", "bounds", ...}
        ...

    def close(self):
        ...


# CDP endpoint discovery on Linux:
def find_chrome_cdp() -> Optional[str]:
    \"\"\"Discover a running Chrome instance with CDP enabled.

    Strategy:
      1. Check common Chrome command lines via ps aux:
         ``ps aux | grep -E 'chrome|chromium' | grep remote-debugging``
      2. Extract the port number from ``--remote-debugging-port=N``.
      3. Probe ``http://127.0.0.1:<port>/json/version``.
      4. Return the WS URL or None.
    \"\"\"
    ...

# If no Chrome with CDP is running, fall back to the existing
# window-level wmctrl list (current behaviour unchanged).
```

Integration with computer_use
-----------------------------
In ``computer_use_backend.py``, ``capture(mode='som')`` would:

1. Get the ``wmctrl`` window list (existing).
2. If Chrome is available via CDP, get AX tree for the active tab.
3. Merge: keep wmctrl windows for non-Chrome apps, replace the Chrome
   window entry with the AX tree elements.
4. Render SOM overlays on this merged element list.

Environment gate: ``JARVIS_AX_TREE_ENABLED=1``. Without it, the current
window-level SOM is used unchanged (safe default).

Verdict: Medium-high value, non-trivial risk. Worth doing *after* the
SOM window-level changes prove stable in production.
"""

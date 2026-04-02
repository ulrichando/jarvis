#!/usr/bin/env python3
"""JARVIS Desktop — unified transparent overlay.

One JARVIS instance. The arc reactor floats on your desktop.
Click it to chat. Drag to move. Voice to control.

Controls:
  Click reactor    → Open/close chat
  Drag reactor     → Move anywhere
  Scroll on reactor → Resize
  "jarvis hide"    → Go invisible
  "jarvis show"    → Reappear
  Ctrl+H           → Toggle visibility
  Ctrl+Q           → Quit
"""

import sys
import os
import threading
import time
import logging

logging.disable(logging.WARNING)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Gdk, WebKit2, GLib


def _start_server(host="127.0.0.1", port=8765):
    import asyncio
    async def _run():
        try:
            from shells.web.server import JarvisWebServer
            server = JarvisWebServer()
            await server.run()
        except Exception as e:
            print(f"Server error: {e}")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())


def _server_running(host="127.0.0.1", port=8765):
    try:
        import urllib.request
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
        return True
    except Exception:
        return False


def _wait_for_server(host="127.0.0.1", port=8765, timeout=15):
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    host = "127.0.0.1"
    port = 8765

    if not _server_running(host, port):
        server_thread = threading.Thread(target=_start_server, args=(host, port), daemon=True)
        server_thread.start()
        print("Starting JARVIS server...")
        if not _wait_for_server(host, port):
            print("Failed to start server.")
            sys.exit(1)

    # ── Window ──
    window = Gtk.Window()
    window.set_title("J.A.R.V.I.S.")
    window.set_default_size(350, 350)
    window.set_decorated(False)
    window.set_app_paintable(True)
    window.set_resizable(False)
    window.set_keep_above(True)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)

    # Position bottom-right
    screen = Gdk.Screen.get_default()
    if screen:
        sw = screen.get_width()
        sh = screen.get_height()
        window.move(sw - 380, sh - 420)

    # Transparency
    visual = screen.get_rgba_visual() if screen else None
    if visual:
        window.set_visual(visual)

    def on_draw(widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(1)  # CAIRO_OPERATOR_SOURCE
        cr.paint()
        return False
    window.connect("draw", on_draw)

    # ── WebKit ──
    webview = WebKit2.WebView()
    webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))

    settings = webview.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_webaudio(True)
    settings.set_enable_media_stream(True)

    # Force React UI (same as browser) with desktop param + cache bust
    import time as _time
    webview.load_uri(f"http://{host}:{port}/?desktop=1&_t={int(_time.time())}")

    # Disable WebKit cache so it always loads fresh
    ctx = webview.get_context()
    ctx.get_website_data_manager().clear(WebKit2.WebsiteDataTypes.ALL, 0, None, None, None)
    window.add(webview)

    # ── Server coordination — hide reactor when browser takes over ──
    _reactor_visible = [True]

    def _poll_client_status():
        """Check if browser is active — if so, hide desktop reactor."""
        try:
            import urllib.request, json as _json
            req = urllib.request.Request(
                f"http://{host}:{port}/api/client/status",
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=2)
            data = _json.loads(resp.read())
            browser_active = data.get("browser", False)

            if browser_active and _reactor_visible[0]:
                _reactor_visible[0] = False
                GLib.idle_add(lambda: window.hide() or False)
            elif not browser_active and not _reactor_visible[0]:
                _reactor_visible[0] = True
                GLib.idle_add(lambda: window.show_all() or False)
        except Exception:
            pass
        return True  # Keep polling

    # Register desktop with server
    try:
        import urllib.request, json as _json
        data = _json.dumps({"type": "desktop"}).encode()
        req = urllib.request.Request(
            f"http://{host}:{port}/api/client/register",
            data=data, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass

    # Poll every 2 seconds
    GLib.timeout_add_seconds(2, _poll_client_status)

    # ── Dragging ──
    _drag = {"active": False, "x": 0, "y": 0}

    def on_button_press(widget, event):
        if event.button == 1:
            _drag["active"] = True
            _drag["x"] = event.x_root
            _drag["y"] = event.y_root

    def on_button_release(widget, event):
        _drag["active"] = False

    def on_motion(widget, event):
        if _drag["active"]:
            x, y = window.get_position()
            dx = event.x_root - _drag["x"]
            dy = event.y_root - _drag["y"]
            window.move(int(x + dx), int(y + dy))
            _drag["x"] = event.x_root
            _drag["y"] = event.y_root

    # ── Resize with scroll ──
    _size = {"w": 350, "h": 350}

    def on_scroll(widget, event):
        if event.direction == Gdk.ScrollDirection.UP:
            _size["w"] = min(1200, _size["w"] + 50)
            _size["h"] = min(1200, _size["h"] + 50)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            _size["w"] = max(200, _size["w"] - 50)
            _size["h"] = max(200, _size["h"] - 50)
        window.resize(_size["w"], _size["h"])

    window.add_events(
        Gdk.EventMask.BUTTON_PRESS_MASK |
        Gdk.EventMask.BUTTON_RELEASE_MASK |
        Gdk.EventMask.POINTER_MOTION_MASK |
        Gdk.EventMask.SCROLL_MASK
    )
    window.connect("button-press-event", on_button_press)
    window.connect("button-release-event", on_button_release)
    window.connect("motion-notify-event", on_motion)
    window.connect("scroll-event", on_scroll)

    # ── Keyboard ──
    _visible = [True]

    def on_key(widget, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.keyval == Gdk.KEY_q:
                Gtk.main_quit()
            elif event.keyval == Gdk.KEY_h:
                _visible[0] = not _visible[0]
                window.set_visible(_visible[0])
            elif event.keyval == Gdk.KEY_minus:
                window.set_opacity(max(0.1, window.get_opacity() - 0.1))
            elif event.keyval in (Gdk.KEY_equal, Gdk.KEY_plus):
                window.set_opacity(min(1.0, window.get_opacity() + 0.1))

    window.connect("key-press-event", on_key)

    def on_destroy(widget):
        # Unregister from server
        try:
            import urllib.request, json as _json
            data = _json.dumps({"type": "desktop"}).encode()
            req = urllib.request.Request(
                f"http://{host}:{port}/api/client/unregister",
                data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass
        Gtk.main_quit()

    window.connect("destroy", on_destroy)

    # ── Listen for hide/show commands from web UI via JS ──
    def on_js_message(webview, js_result):
        """Handle messages from the web UI JavaScript."""
        try:
            msg = js_result.get_js_value().to_string()
            if msg == "hide":
                _visible[0] = False
                window.set_visible(False)
            elif msg == "show":
                _visible[0] = True
                window.set_visible(True)
            elif msg == "minimize":
                _size["w"] = 200
                _size["h"] = 200
                window.resize(200, 200)
            elif msg == "maximize":
                _size["w"] = 800
                _size["h"] = 800
                window.resize(800, 800)
        except Exception:
            pass

    # ── Show ──
    window.show_all()
    print("JARVIS desktop running.")
    print("  Drag reactor to move")
    print("  Scroll to resize")
    print("  Ctrl+H hide/show")
    print("  Ctrl+Q quit")
    Gtk.main()


if __name__ == "__main__":
    main()

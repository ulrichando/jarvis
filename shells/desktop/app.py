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


import json as _json

DESKTOP_CONFIG = os.path.expanduser("~/.jarvis/desktop.json")

def _load_desktop_config():
    try:
        if os.path.exists(DESKTOP_CONFIG):
            return _json.loads(open(DESKTOP_CONFIG).read())
    except Exception:
        pass
    return {"width": 700, "height": 700, "x": -1, "y": -1, "opacity": 1.0}

def _save_desktop_config(config):
    try:
        os.makedirs(os.path.dirname(DESKTOP_CONFIG), exist_ok=True)
        open(DESKTOP_CONFIG, "w").write(_json.dumps(config, indent=2))
    except Exception:
        pass


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

    # ── Window (load saved size/position) ──
    _cfg = _load_desktop_config()

    window = Gtk.Window()
    window.set_title("J.A.R.V.I.S.")
    window.set_default_size(_cfg.get("width", 700), _cfg.get("height", 700))
    window.set_decorated(False)
    window.set_app_paintable(True)
    window.set_resizable(False)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)

    if _cfg.get("x", -1) >= 0 and _cfg.get("y", -1) >= 0:
        window.move(_cfg["x"], _cfg["y"])
    else:
        window.set_position(Gtk.WindowPosition.CENTER)
    screen = Gdk.Screen.get_default()

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
    settings.set_enable_mediasource(True)
    # Allow local resource access
    settings.set_allow_file_access_from_file_urls(True)
    settings.set_allow_universal_access_from_file_urls(True)

    # Auto-grant audio/video permissions (so mic works for pulsing)
    def _on_permission_request(webview, request):
        """Auto-grant media permissions for the desktop overlay."""
        request.allow()
        return True
    webview.connect("permission-request", _on_permission_request)

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
        # Save position after drag
        x, y = window.get_position()
        _cfg.update({"x": x, "y": y})
        _save_desktop_config(_cfg)

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

    # ── System Tray Icon with full menu ──
    _jarvis_icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "jarvis-icon-48.png"
    )

    def _build_tray_menu():
        """Build the right-click tray menu."""
        menu = Gtk.Menu()

        item_show = Gtk.MenuItem(label="Show / Hide JARVIS")
        item_show.connect("activate", lambda w: window.set_visible(not window.get_visible()))
        menu.append(item_show)

        menu.append(Gtk.SeparatorMenuItem())

        def _save_state():
            """Save current window state to config."""
            x, y = window.get_position()
            _cfg.update({"width": _size["w"], "height": _size["h"], "x": x, "y": y,
                         "opacity": window.get_opacity()})
            _save_desktop_config(_cfg)

        item_bigger = Gtk.MenuItem(label="Bigger")
        def _bigger(w):
            _size["w"] = min(1200, _size["w"] + 100)
            _size["h"] = min(1200, _size["h"] + 100)
            window.resize(_size["w"], _size["h"])
            _save_state()
        item_bigger.connect("activate", _bigger)
        menu.append(item_bigger)

        item_smaller = Gtk.MenuItem(label="Smaller")
        def _smaller(w):
            _size["w"] = max(200, _size["w"] - 100)
            _size["h"] = max(200, _size["h"] - 100)
            window.resize(_size["w"], _size["h"])
            _save_state()
        item_smaller.connect("activate", _smaller)
        menu.append(item_smaller)

        item_center = Gtk.MenuItem(label="Center on Screen")
        def _center(w):
            window.set_position(Gtk.WindowPosition.CENTER)
            GLib.timeout_add(200, _save_state)  # Save after GTK repositions
        item_center.connect("activate", _center)
        menu.append(item_center)

        menu.append(Gtk.SeparatorMenuItem())

        item_opacity_up = Gtk.MenuItem(label="More Opaque")
        def _more_opaque(w):
            window.set_opacity(min(1.0, window.get_opacity() + 0.2))
            _save_state()
        item_opacity_up.connect("activate", _more_opaque)
        menu.append(item_opacity_up)

        item_opacity_down = Gtk.MenuItem(label="More Transparent")
        def _more_transparent(w):
            window.set_opacity(max(0.1, window.get_opacity() - 0.2))
            _save_state()
        item_opacity_down.connect("activate", _more_transparent)
        menu.append(item_opacity_down)

        menu.append(Gtk.SeparatorMenuItem())

        item_open_browser = Gtk.MenuItem(label="Open in Browser")
        item_open_browser.connect("activate", lambda w: os.system(f"xdg-open http://{host}:{port}/ &"))
        menu.append(item_open_browser)

        item_cli = Gtk.MenuItem(label="Open JARVIS CLI")
        item_cli.connect("activate", lambda w: os.system("x-terminal-emulator -e jarvis &"))
        menu.append(item_cli)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit JARVIS")
        item_quit.connect("activate", lambda w: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        return menu

    # Try AppIndicator3 first (modern), fallback to StatusIcon
    try:
        gi.require_version('AppIndicator3', '0.1')
        from gi.repository import AppIndicator3
        indicator = AppIndicator3.Indicator.new(
            "jarvis-desktop",
            _jarvis_icon_path if os.path.exists(_jarvis_icon_path) else "applications-system",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        indicator.set_title("J.A.R.V.I.S.")
        indicator.set_menu(_build_tray_menu())
    except Exception:
        try:
            if os.path.exists(_jarvis_icon_path):
                tray = Gtk.StatusIcon.new_from_file(_jarvis_icon_path)
            else:
                tray = Gtk.StatusIcon.new_from_icon_name("applications-system")
            tray.set_tooltip_text("J.A.R.V.I.S.")
            tray.set_visible(True)
            tray.connect("activate", lambda w: window.set_visible(not window.get_visible()))

            def _on_tray_popup(icon, button, activate_time):
                menu = _build_tray_menu()
                menu.popup(None, None, None, None, button, activate_time)
            tray.connect("popup-menu", _on_tray_popup)
        except Exception:
            pass

    # ── Click-through mode (input passes through to desktop) ──
    def _set_click_through(enabled):
        """Make window click-through so clicks pass to apps below."""
        if enabled:
            from ctypes import cdll, c_ulong, c_int
            try:
                xlib = cdll.LoadLibrary("libX11.so.6")
                display = xlib.XOpenDisplay(None)
                if display:
                    xid = window.get_window().get_xid()
                    # Set input region to empty rectangle (click-through)
                    xfixes = cdll.LoadLibrary("libXfixes.so.3")
                    region = xfixes.XFixesCreateRegion(display, None, 0)
                    xfixes.XFixesSetWindowShapeRegion(display, xid, 2, 0, 0, region)  # ShapeInput=2
                    xfixes.XFixesDestroyRegion(display, region)
                    xlib.XFlush(display)
                    xlib.XCloseDisplay(display)
            except Exception:
                pass

    # Enable click-through by default
    def _enable_click_through_on_map(widget, event=None):
        GLib.timeout_add(500, lambda: _set_click_through(True) or False)
    window.connect("map-event", _enable_click_through_on_map)

    # ── Show ──
    _size = {"w": _cfg.get("width", 700), "h": _cfg.get("height", 700)}

    # Apply saved opacity
    if _cfg.get("opacity", 1.0) < 1.0:
        window.set_opacity(_cfg["opacity"])
    window.show_all()
    print("JARVIS desktop running.")
    print("  Tray icon in system tray")
    print("  Scroll to resize")
    print("  Ctrl+H hide/show")
    print("  Ctrl+Q quit")
    print("  Click-through enabled (clicks pass to apps below)")
    Gtk.main()


if __name__ == "__main__":
    main()

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
            from src.server.web_server import JarvisWebServer
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


def _wait_for_server(host="127.0.0.1", port=8765, timeout=60):
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
        print("Starting JARVIS server (MCP init takes ~30s, please wait)...")
        if not _wait_for_server(host, port):
            print("Failed to start server. Check /tmp/jarvis-desktop.log")
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

    # ── WebKit — autoplay allowed + temp cache dir ──
    import tempfile as _tf
    _cache_dir = _tf.mkdtemp(prefix="jarvis-webkit-")
    data_mgr = WebKit2.WebsiteDataManager(
        base_cache_directory=_cache_dir,
        base_data_directory=_cache_dir,
    )
    web_ctx = WebKit2.WebContext.new_with_website_data_manager(data_mgr)
    web_ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
    # Create WebView with BOTH context and autoplay policy
    policies = WebKit2.WebsitePolicies(autoplay=WebKit2.AutoplayPolicy.ALLOW)
    webview = WebKit2.WebView(
        web_context=web_ctx,
        website_policies=policies,
    )
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

    # Enable JS console output and developer tools for debugging
    settings.set_enable_write_console_messages_to_stdout(True)
    settings.set_enable_developer_extras(True)

    # WebProcess crash recovery — auto-reload up to 3 times
    _crash_count = [0]
    def _on_web_process_crashed(wv):
        _crash_count[0] += 1
        if _crash_count[0] <= 3:
            print(f"[JARVIS] WebProcess crashed (attempt {_crash_count[0]}/3) — reloading")
            GLib.timeout_add(1000 * _crash_count[0], lambda: wv.reload() or False)
        else:
            print("[JARVIS] WebProcess crashed too many times — giving up")
        return True
    webview.connect("web-process-crashed", _on_web_process_crashed)

    # Clear cache on the webview's context (the temp cache dir handles freshness)
    ctx = webview.get_context()
    ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

    # Load React UI
    import time as _time
    settings.set_enable_page_cache(False)
    webview.load_uri(f"http://{host}:{port}/?desktop=1&_t={int(_time.time())}")

    # Hide window during initial load to prevent old theme flash
    window.set_opacity(0)

    _reloaded = [False]
    _saved_primary = _cfg.get("theme_primary", "#00e5ff")
    _saved_glow = _cfg.get("theme_glow", "#0088aa")
    _target_opacity = _cfg.get("opacity", 1.0)

    def _on_load(wv, event):
        if event == WebKit2.LoadEvent.FINISHED:
            wv.set_background_color(Gdk.RGBA(0, 0, 0, 0))
            # Apply saved theme colors
            js = f"window.__jarvisSetTheme && window.__jarvisSetTheme('{_saved_primary}', '{_saved_glow}')"
            wv.run_javascript(js, None, None, None)

            if not _reloaded[0]:
                # First load — reload to bust cache, stay hidden
                _reloaded[0] = True
                GLib.timeout_add(500, lambda: wv.reload_bypass_cache() or False)
            else:
                # Second load (fresh) — show window with correct theme
                GLib.timeout_add(300, lambda: window.set_opacity(_target_opacity) or False)
    webview.connect("load-changed", _on_load)
    window.add(webview)

    # ── Register desktop and poll for browser handoff ──
    _reactor_visible = [True]

    try:
        import urllib.request, json as _json
        data = _json.dumps({"type": "desktop"}).encode()
        req = urllib.request.Request(
            f"http://{host}:{port}/api/client/register",
            data=data, headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=2)
        reg = _json.loads(resp.read())
        _reactor_visible[0] = reg.get("show_reactor", True)
        if not _reactor_visible[0]:
            window.hide()
    except Exception:
        pass

    def _poll_client_status():
        """Seamless handoff: hide when browser opens, show when browser closes."""
        try:
            import urllib.request, json as _json
            resp = urllib.request.urlopen(
                f"http://{host}:{port}/api/client/status", timeout=2)
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
        return True

    GLib.timeout_add_seconds(2, _poll_client_status)

    # ── Dragging — use window manager move for reliable drag ──
    def on_button_press(widget, event):
        if event.button == 1:
            # Start window manager drag
            window.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)

    def on_button_release(widget, event):
        # Save position after drag
        x, y = window.get_position()
        _cfg.update({"x": x, "y": y})
        _save_desktop_config(_cfg)

    def on_motion(widget, event):
        pass  # Window manager handles the actual movement

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
        # Clean up temp WebKit cache dir
        try:
            import shutil
            shutil.rmtree(_cache_dir, ignore_errors=True)
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
    from src.desktop.colors import (
        PRESETS, get_theme, get_colors, set_theme, set_custom_color, generate_icon,
    )

    # Generate icon with current theme color
    _jarvis_icon_path = generate_icon()

    # Holder for indicator/tray so color changes can update it
    _tray_ref = [None]

    def _apply_color_change(theme_name=None, custom_hex=None):
        """Apply a color change: regenerate icon, reload webview, update tray."""
        if custom_hex:
            set_custom_color(custom_hex)
        elif theme_name:
            set_theme(theme_name)

        new_icon = generate_icon()

        # Update tray icon (new_icon has a unique color-stamped filename
        # so AppIndicator detects the change instead of serving cached icon)
        tray = _tray_ref[0]
        if tray is not None:
            if hasattr(tray, 'set_icon_full'):
                # AppIndicator — set_icon_full with new unique path
                tray.set_icon_full(new_icon, "JARVIS")
                # Also update icon search path to force refresh
                if hasattr(tray, 'set_icon_theme_path'):
                    tray.set_icon_theme_path(os.path.dirname(new_icon))
                    tray.set_icon_full(new_icon, "JARVIS")
            elif hasattr(tray, 'set_from_file'):
                # StatusIcon
                tray.set_from_file(new_icon)

        # Tell frontend to reload colors
        primary, glow = get_colors()
        js = f"window.__jarvisSetTheme && window.__jarvisSetTheme('{primary}', '{glow}')"
        GLib.idle_add(lambda: webview.run_javascript(js, None, None, None) or False)

    def _build_tray_menu():
        """Build the right-click tray menu."""
        menu = Gtk.Menu()

        item_show = Gtk.MenuItem(label="Show / Hide JARVIS")
        def _toggle_show(w):
            vis = not window.get_visible()
            window.set_visible(vis)
            if vis:
                window.present()  # Bring to front
                # Re-enable click-through after showing
                GLib.timeout_add(500, lambda: _set_click_through(True) or False)
        item_show.connect("activate", _toggle_show)
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
            scr = Gdk.Screen.get_default()
            sw, sh = scr.get_width(), scr.get_height()
            ww, wh = _size["w"], _size["h"]
            window.move((sw - ww) // 2, (sh - wh) // 2)
            GLib.timeout_add(200, _save_state)
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

        # ── Color Theme Submenu ──
        current_theme = get_theme()
        color_item = Gtk.MenuItem(label="Theme Color")
        color_sub = Gtk.Menu()

        for preset_id, (_, _, label) in PRESETS.items():
            prefix = "\u2022 " if preset_id == current_theme else "  "
            item = Gtk.MenuItem(label=f"{prefix}{label}")
            pid = preset_id  # capture
            item.connect("activate", lambda w, t=pid: _apply_color_change(theme_name=t))
            color_sub.append(item)

        color_sub.append(Gtk.SeparatorMenuItem())

        item_custom = Gtk.MenuItem(label="  Custom Color...")
        def _pick_custom(w):
            dialog = Gtk.ColorChooserDialog(title="JARVIS Color", parent=window)
            primary, _ = get_colors()
            from src.desktop.colors import hex_to_rgb
            r, g, b = hex_to_rgb(primary)
            dialog.set_rgba(Gdk.RGBA(r / 255, g / 255, b / 255, 1.0))
            if dialog.run() == Gtk.ResponseType.OK:
                rgba = dialog.get_rgba()
                hex_color = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"
                _apply_color_change(custom_hex=hex_color)
            dialog.destroy()
        item_custom.connect("activate", _pick_custom)
        color_sub.append(item_custom)

        color_item.set_submenu(color_sub)
        menu.append(color_item)

        menu.append(Gtk.SeparatorMenuItem())

        item_open_browser = Gtk.MenuItem(label="Open in Browser")
        item_open_browser.connect("activate", lambda w: os.system(f"xdg-open http://{host}:{port}/ &"))
        menu.append(item_open_browser)

        item_cli = Gtk.MenuItem(label="Open JARVIS CLI")
        item_cli.connect("activate", lambda w: os.system("x-terminal-emulator -e jarvis &"))
        menu.append(item_cli)

        menu.append(Gtk.SeparatorMenuItem())

        # Move mode — temporarily disable click-through for dragging
        item_move = Gtk.MenuItem(label="Move JARVIS (5s drag)")
        def _move_mode(w):
            _set_click_through(False)
            window.present()
            # Re-enable click-through after 5 seconds
            def _relock():
                _set_click_through(True)
                _save_state()
                return False
            GLib.timeout_add(5000, _relock)
        item_move.connect("activate", _move_mode)
        menu.append(item_move)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit JARVIS")
        item_quit.connect("activate", lambda w: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        return menu

    # Try AppIndicator3 first (modern), fallback to StatusIcon
    try:
        # Ayatana is the actively maintained fork on modern distros
        try:
            gi.require_version('AyatanaAppIndicator3', '0.1')
            from gi.repository import AyatanaAppIndicator3 as AppIndicator3
        except (ValueError, ImportError):
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
        _tray_ref[0] = indicator
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
            _tray_ref[0] = tray
        except Exception:
            pass

    # ── Click-through mode (input passes through to desktop) ──
    def _set_click_through(enabled):
        """Make window click-through. Supports both X11 and Wayland."""
        try:
            if os.environ.get("WAYLAND_DISPLAY"):
                # Wayland: use GTK input shape region
                import cairo
                gdk_win = window.get_window()
                if gdk_win:
                    if enabled:
                        region = cairo.Region(cairo.RectangleInt(0, 0, 0, 0))
                        gdk_win.input_shape_combine_region(region, 0, 0)
                    else:
                        gdk_win.input_shape_combine_region(None, 0, 0)
            else:
                # X11: use XFixes input shape
                if enabled:
                    from ctypes import cdll
                    xlib = cdll.LoadLibrary("libX11.so.6")
                    display = xlib.XOpenDisplay(None)
                    if display:
                        xid = window.get_window().get_xid()
                        xfixes = cdll.LoadLibrary("libXfixes.so.3")
                        region = xfixes.XFixesCreateRegion(display, None, 0)
                        xfixes.XFixesSetWindowShapeRegion(display, xid, 2, 0, 0, region)
                        xfixes.XFixesDestroyRegion(display, region)
                        xlib.XFlush(display)
                        xlib.XCloseDisplay(display)
        except Exception:
            pass

    # Enable click-through by default — clicks pass through to apps below
    def _enable_click_through_on_map(widget, event=None):
        GLib.timeout_add(500, lambda: _set_click_through(True) or False)
    window.connect("map-event", _enable_click_through_on_map)

    # ── Show ──
    _size = {"w": _cfg.get("width", 700), "h": _cfg.get("height", 700)}

    # Window starts hidden (opacity 0) — shown after reload with correct theme
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

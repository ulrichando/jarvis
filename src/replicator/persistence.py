"""JARVIS Persistence — survive reboots, auto-start, stay alive.

After deployment, JARVIS needs to:
- Start on boot
- Restart if killed
- Reconnect to the network
- Phone home to report status
"""

import subprocess
import os
from pathlib import Path


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def install_systemd_service(jarvis_dir: str, user: str = None) -> dict:
    """Install a systemd user service for JARVIS."""
    if not user:
        user = os.environ.get("USER", "root")

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)

    service_content = f"""[Unit]
Description=JARVIS AI Brain
After=network.target

[Service]
Type=simple
WorkingDirectory={jarvis_dir}
ExecStart={jarvis_dir}/.venv/bin/python -m shells.web.server
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""

    service_path = service_dir / "jarvis.service"
    service_path.write_text(service_content)

    _run("systemctl --user daemon-reload")
    _run("systemctl --user enable jarvis.service")
    _run("systemctl --user start jarvis.service")

    return {"success": True, "service": str(service_path)}


def install_crontab(jarvis_dir: str) -> dict:
    """Install a crontab entry as fallback for systems without systemd."""
    venv_python = f"{jarvis_dir}/.venv/bin/python"
    cron_line = f"@reboot cd {jarvis_dir} && {venv_python} -m shells.web.server &"

    existing = _run("crontab -l 2>/dev/null")
    if "shells.web.server" in existing:
        return {"success": True, "message": "Already in crontab."}

    new_cron = existing + "\n" + cron_line + "\n" if existing else cron_line + "\n"
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cron", delete=False) as f:
        f.write(new_cron)
        f.flush()
        _run(f"crontab {f.name}")

    return {"success": True, "message": "Added to crontab."}


def install_autostart_desktop(jarvis_dir: str) -> dict:
    """Install XDG autostart entry (Linux desktop)."""
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)

    desktop_content = f"""[Desktop Entry]
Type=Application
Name=JARVIS
Exec={jarvis_dir}/.venv/bin/python -m shells.web.server
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""

    desktop_path = autostart_dir / "jarvis.desktop"
    desktop_path.write_text(desktop_content)

    return {"success": True, "path": str(desktop_path)}


def install_windows_startup(jarvis_dir: str) -> dict:
    """Create Windows startup shortcut."""
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    if not startup.exists():
        return {"success": False, "error": "Windows startup folder not found."}

    bat_content = f"""@echo off
cd /d {jarvis_dir}
.venv\\Scripts\\python.exe -m shells.web.server
"""
    bat_path = startup / "jarvis.bat"
    bat_path.write_text(bat_content)

    return {"success": True, "path": str(bat_path)}


def install_android_boot(jarvis_dir: str) -> dict:
    """Termux:Boot auto-start for Android."""
    boot_dir = Path.home() / ".termux" / "boot"
    boot_dir.mkdir(parents=True, exist_ok=True)

    script = f"""#!/data/data/com.termux/files/usr/bin/bash
cd {jarvis_dir}
python -m shells.web.server &
"""
    script_path = boot_dir / "jarvis.sh"
    script_path.write_text(script)
    os.chmod(str(script_path), 0o755)

    return {"success": True, "path": str(script_path)}


def install_persistence(jarvis_dir: str, os_type: str = "linux") -> dict:
    """Install appropriate persistence for the target OS."""
    results = {}

    if os_type == "linux":
        results["systemd"] = install_systemd_service(jarvis_dir)
        results["crontab"] = install_crontab(jarvis_dir)
        results["autostart"] = install_autostart_desktop(jarvis_dir)
    elif os_type == "windows":
        results["startup"] = install_windows_startup(jarvis_dir)
    elif os_type == "android":
        results["boot"] = install_android_boot(jarvis_dir)
    elif os_type == "macos":
        results["crontab"] = install_crontab(jarvis_dir)

    return results

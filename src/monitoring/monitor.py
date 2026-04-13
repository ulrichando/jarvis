"""JARVIS Proactive Monitor — background engine that watches system health
and fires alerts through the active channel without being asked.

Config: ~/.jarvis/monitors.yaml
Each monitor has:
  name:      unique identifier
  type:      cpu | memory | disk | service | port | http | log_pattern | process
  interval:  seconds between checks (default: 60)
  cooldown:  seconds before re-alerting the same monitor (default: 300)
  level:     warning | error | info (default: warning)
  message:   optional override message (supports {alert} placeholder)
  threshold: numeric threshold for cpu/memory/disk (percent)
  ...type-specific keys...
"""

import asyncio
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ── Alert callback type: fn(level, name, message) ─────────────────────
AlertCallback = Callable[[str, str, str], None]


class Monitor:
    """Async background monitoring engine."""

    def __init__(self, config_path: str = "~/.jarvis/monitors.yaml"):
        self.config_path = os.path.expanduser(config_path)
        self._alert_callback: Optional[AlertCallback] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._next_check: dict[str, float] = {}
        self._last_alerted: dict[str, float] = {}
        self._default_cooldown = 300  # 5 min between repeat alerts

    def set_alert_callback(self, fn: AlertCallback) -> None:
        """Register the function called when an alert fires."""
        self._alert_callback = fn

    # ── Config ────────────────────────────────────────────────────────

    def _load_config(self) -> list[dict]:
        if not os.path.exists(self.config_path):
            return []
        try:
            import yaml
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}
            monitors = data.get("monitors", [])
            return [m for m in monitors if not m.get("disabled", False)]
        except Exception as e:
            log.warning("monitors.yaml load failed: %s", e)
            return []

    # ── Individual checks ─────────────────────────────────────────────

    async def _check_cpu(self, monitor: dict) -> Optional[str]:
        threshold = monitor.get("threshold", 85)
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            fields = list(map(int, line.split()[1:]))
            idle = fields[3]
            total = sum(fields)
            await asyncio.sleep(0.5)
            with open("/proc/stat") as f:
                line2 = f.readline()
            fields2 = list(map(int, line2.split()[1:]))
            idle2 = fields2[3]
            total2 = sum(fields2)
            cpu = (1 - (idle2 - idle) / (total2 - total)) * 100
            if cpu >= threshold:
                return f"CPU at {cpu:.0f}% — threshold is {threshold}%"
        except Exception:
            pass
        return None

    async def _check_memory(self, monitor: dict) -> Optional[str]:
        threshold = monitor.get("threshold", 90)
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    k, v = line.split(":")
                    info[k.strip()] = int(v.strip().split()[0])
            total = info.get("MemTotal", 0)
            available = info.get("MemAvailable", 0)
            if total > 0:
                used_pct = (total - available) / total * 100
                if used_pct >= threshold:
                    free_gb = available / 1024 / 1024
                    return f"Memory at {used_pct:.0f}% — {free_gb:.1f}GB free"
        except Exception:
            pass
        return None

    async def _check_disk(self, monitor: dict) -> Optional[str]:
        path = monitor.get("path", "/")
        threshold = monitor.get("threshold", 90)
        try:
            usage = shutil.disk_usage(path)
            pct = usage.used / usage.total * 100
            if pct >= threshold:
                free_gb = usage.free / 1024 ** 3
                return f"Disk {path} at {pct:.0f}% — {free_gb:.1f}GB free"
        except Exception:
            pass
        return None

    async def _check_service(self, monitor: dict) -> Optional[str]:
        service = monitor.get("service", monitor.get("name", ""))
        if not service:
            return None
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                timeout=5,
            )
            if r.returncode != 0:
                r2 = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True, text=True, timeout=5,
                )
                status = r2.stdout.strip() or "inactive"
                return f"Service '{service}' is {status}"
        except FileNotFoundError:
            pass  # No systemd — skip silently
        except Exception as e:
            log.debug("service check '%s' failed: %s", service, e)
        return None

    async def _check_port(self, monitor: dict) -> Optional[str]:
        host = monitor.get("host", "localhost")
        port = monitor.get("port", 80)
        timeout = monitor.get("timeout", 3)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: socket.create_connection((host, port), timeout=timeout).close(),
            )
            return None  # Open — no alert
        except Exception:
            return f"Port {host}:{port} unreachable"

    async def _check_http(self, monitor: dict) -> Optional[str]:
        import urllib.request
        url = monitor.get("url", "")
        expected = monitor.get("expected_status", 200)
        timeout = monitor.get("timeout", 10)
        if not url:
            return None
        try:
            loop = asyncio.get_event_loop()
            def _fetch():
                try:
                    r = urllib.request.urlopen(url, timeout=timeout)
                    return r.status
                except urllib.error.HTTPError as e:
                    return e.code
                except Exception:
                    return None
            status = await loop.run_in_executor(None, _fetch)
            if status is None:
                return f"{url} unreachable"
            if status != expected:
                return f"{url} returned HTTP {status} (expected {expected})"
        except Exception as e:
            return f"{url} check failed: {e}"
        return None

    async def _check_log_pattern(self, monitor: dict) -> Optional[str]:
        path = monitor.get("path", "")
        pattern = monitor.get("pattern", "")
        lines = monitor.get("lines", 100)
        if not path or not pattern:
            return None
        try:
            r = subprocess.run(
                ["tail", f"-n{lines}", path],
                capture_output=True, text=True, timeout=5,
            )
            matches = [l for l in r.stdout.splitlines() if re.search(pattern, l, re.IGNORECASE)]
            if matches:
                return f"Pattern '{pattern}' matched in {path}: {matches[-1][:120]}"
        except Exception:
            pass
        return None

    async def _check_process(self, monitor: dict) -> Optional[str]:
        process = monitor.get("process", monitor.get("name", ""))
        if not process:
            return None
        try:
            r = subprocess.run(["pgrep", "-f", process], capture_output=True, timeout=5)
            if r.returncode != 0:
                return f"Process '{process}' is not running"
        except Exception:
            pass
        return None

    # ── Dispatch ──────────────────────────────────────────────────────

    async def _run_check(self, monitor: dict) -> Optional[str]:
        t = monitor.get("type", "")
        handlers = {
            "cpu":         self._check_cpu,
            "memory":      self._check_memory,
            "disk":        self._check_disk,
            "service":     self._check_service,
            "port":        self._check_port,
            "http":        self._check_http,
            "log_pattern": self._check_log_pattern,
            "process":     self._check_process,
        }
        fn = handlers.get(t)
        if fn:
            return await fn(monitor)
        log.debug("Unknown monitor type: %s", t)
        return None

    def _should_alert(self, name: str, cooldown: int) -> bool:
        return time.time() - self._last_alerted.get(name, 0) >= cooldown

    def _fire_alert(self, level: str, name: str, message: str) -> None:
        self._last_alerted[name] = time.time()
        log.warning("ALERT [%s/%s]: %s", level.upper(), name, message)
        if self._alert_callback:
            try:
                self._alert_callback(level, name, message)
            except Exception as e:
                log.debug("Alert callback error: %s", e)

    # ── Main loop ─────────────────────────────────────────────────────

    async def _loop(self) -> None:
        log.info("Monitor engine started — watching %s", self.config_path)
        while self._running:
            try:
                monitors = self._load_config()
                now = time.time()

                for monitor in monitors:
                    name = monitor.get("name", "unnamed")
                    interval = monitor.get("interval", 60)
                    cooldown = monitor.get("cooldown", self._default_cooldown)
                    level = monitor.get("level", "warning")

                    if now < self._next_check.get(name, 0):
                        continue

                    self._next_check[name] = now + interval

                    try:
                        alert = await self._run_check(monitor)
                        if alert and self._should_alert(name, cooldown):
                            custom = monitor.get("message", "")
                            msg = custom.format(alert=alert) if custom else alert
                            self._fire_alert(level, name, msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.debug("Monitor '%s' error: %s", name, e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Monitor loop error: %s", e)

            await asyncio.sleep(10)

        log.info("Monitor engine stopped.")

    def start(self) -> asyncio.Task:
        """Start background monitoring. Must be called from async context."""
        if self._running:
            return self._task
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        self._task.add_done_callback(lambda t: log.debug("Monitor task ended: %s", t.exception() if not t.cancelled() else "cancelled"))
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


# ── Singleton ─────────────────────────────────────────────────────────

_monitor: Optional[Monitor] = None


def get_monitor() -> Monitor:
    global _monitor
    if _monitor is None:
        _monitor = Monitor()
    return _monitor

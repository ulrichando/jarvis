"""
Copy ANSI text as a PNG image to the system clipboard.
Supports Linux (xclip/xsel), macOS (osascript), and Windows (PowerShell).
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClipboardResult:
    success: bool
    message: str


def _get_platform() -> str:
    """Detect the platform: linux, macos, or windows."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def copy_png_to_clipboard(png_path: str) -> ClipboardResult:
    """
    Copy a PNG file to the system clipboard.

    Args:
        png_path: Path to the PNG file.

    Returns:
        ClipboardResult with success status and message.
    """
    plat = _get_platform()

    if plat == "macos":
        escaped_path = png_path.replace("\\", "\\\\").replace('"', '\\"')
        script = f'set the clipboard to (read (POSIX file "{escaped_path}") as <<class PNGf>>)'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return ClipboardResult(success=True, message="Screenshot copied to clipboard")
            return ClipboardResult(
                success=False,
                message=f"Failed to copy to clipboard: {result.stderr}",
            )
        except Exception as e:
            return ClipboardResult(success=False, message=f"Failed: {e}")

    if plat == "linux":
        # Try xclip first
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-i", png_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return ClipboardResult(success=True, message="Screenshot copied to clipboard")
        except FileNotFoundError:
            pass

        # Try xsel as fallback
        try:
            result = subprocess.run(
                ["xsel", "--clipboard", "--input", "--type", "image/png"],
                input=open(png_path, "rb").read(),
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return ClipboardResult(success=True, message="Screenshot copied to clipboard")
        except FileNotFoundError:
            pass

        return ClipboardResult(
            success=False,
            message="Failed to copy to clipboard. Please install xclip or xsel: sudo apt install xclip",
        )

    if plat == "windows":
        escaped = png_path.replace("'", "''")
        ps_script = (
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.Clipboard]::SetImage("
            f"[System.Drawing.Image]::FromFile('{escaped}'))"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return ClipboardResult(success=True, message="Screenshot copied to clipboard")
            return ClipboardResult(
                success=False,
                message=f"Failed to copy to clipboard: {result.stderr}",
            )
        except Exception as e:
            return ClipboardResult(success=False, message=f"Failed: {e}")

    return ClipboardResult(
        success=False,
        message=f"Screenshot to clipboard is not supported on {plat}",
    )

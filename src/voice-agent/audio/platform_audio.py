"""Cross-platform audio device + AEC probes (Phase 2.2 abstraction).

Backends:
  Linux:   PipeWire via pw-dump (existing behavior, preserved exactly).
           pactl fallback retained for PulseAudio-only systems (the
           current dev box is PipeWire-native, so pactl is unreachable
           here, but other Linux installs may not have pw-dump).
  Windows: WASAPI inspection via sounddevice.query_devices().
  macOS:   CoreAudio inspection via sounddevice.query_devices().

On non-Linux: l1_echo_cancel_active() returns False. Windows + macOS have
no OS-level echo cancellation equivalent to PipeWire's
module-echo-cancel, so the cascade falls through to L2 (WebRTC APM AEC)
and L3 (DTLN neural residual), both of which ARE cross-platform.

This module is the SINGLE place that touches platform-specific audio
binaries; aec_health.py and output_profile.py consume the dispatched
results.
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess

logger = logging.getLogger("jarvis.audio.platform_audio")


# ---------- Linux backend (PipeWire / PulseAudio) -----------------------

def _linux_default_source() -> str:
    """The PipeWire default.audio.source node name via pw-dump. Empty on
    any failure. Split out for test mocking."""
    try:
        raw = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=2  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)
        ).stdout
        nodes = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(nodes, list):
        return ""
    for n in nodes:
        if isinstance(n, dict) and n.get("type") == "PipeWire:Interface:Metadata":
            for entry in n.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    val = entry.get("value")
                    if isinstance(val, dict):
                        return (val.get("name") or "").strip()
                    if isinstance(val, str):
                        return val.strip()
    return ""


def _linux_pwdump_active_sink_desc() -> str:
    """Return a free-text descriptor of the active output sink via pw-dump
    (PipeWire-native). Empty string if pw-dump is absent or anything fails.

    Resolves the default sink from the Metadata node's `default.audio.sink`
    entry, then returns that sink's `node.description` plus (when available)
    the linked device's form-factor token so the classifier can see it.
    If the default sink is the echo-cancel virtual sink (or no default is
    set), prefer the first real (non-echo-cancel) Audio/Sink node.
    """
    try:
        raw = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=2  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)
        ).stdout
        nodes = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(nodes, list):
        return ""

    # 1. Default sink name from the Metadata node.
    default_name = ""
    for n in nodes:
        if n.get("type") == "PipeWire:Interface:Metadata":
            for entry in n.get("metadata") or []:
                if entry.get("key") == "default.audio.sink":
                    val = entry.get("value")
                    if isinstance(val, dict):
                        default_name = (val.get("name") or "").strip()
                    elif isinstance(val, str):
                        default_name = val.strip()

    # 2. Collect Audio/Sink nodes keyed by node.name.
    sinks = {}
    for n in nodes:
        props = (n.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Sink":
            name = props.get("node.name") or ""
            sinks[name] = props

    if not sinks:
        return ""

    def _is_echo_cancel(name: str) -> bool:
        return "echo-cancel" in (name or "").lower()

    # 3. Pick the target sink: the metadata default unless it's the
    #    echo-cancel virtual sink; otherwise the first real device; else
    #    the first sink of any kind.
    target = ""
    if default_name and default_name in sinks and not _is_echo_cancel(default_name):
        target = default_name
    else:
        real = [nm for nm in sinks if not _is_echo_cancel(nm)]
        target = real[0] if real else next(iter(sinks))

    props = sinks.get(target, {})
    parts = [props.get("node.description") or "", props.get("node.name") or ""]

    # 4. Append the linked device's form-factor so the classifier sees it.
    #    pw-dump uses the hyphenated `device.form-factor`; pactl uses the
    #    underscored `device.form_factor` — accept either.
    dev_id = props.get("device.id")
    if dev_id is not None:
        for n in nodes:
            if n.get("id") == dev_id:
                dprops = (n.get("info") or {}).get("props") or {}
                ff = dprops.get("device.form-factor") or dprops.get("device.form_factor")
                if ff:
                    parts.append(str(ff))
                desc = dprops.get("device.description")
                if desc:
                    parts.append(str(desc))
                break

    return " ".join(p for p in parts if p).strip()


def _linux_pactl_active_sink_block() -> str:
    """Return the full `pactl list sinks` block for the default sink.
    Empty string if pactl is unavailable. Split out for test mocking.

    Kept as a fallback for PulseAudio-only Linux installs (the dev box is
    PipeWire-native, so pactl is unreachable here, but other Linux hosts
    may not have pw-dump available)."""
    try:
        default = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2  # windows-footgun: ok
        ).stdout.strip()
        full = subprocess.run(
            ["pactl", "list", "sinks"], capture_output=True, text=True, timeout=2  # windows-footgun: ok
        ).stdout
    except Exception:
        return ""
    # Slice out the block for the default-named sink.
    blocks = full.split("Sink #")
    for b in blocks:
        if default and default in b:
            return b
    return blocks[1] if len(blocks) > 1 else ""


# ---------- Windows backend (WASAPI via sounddevice) -------------------

def _sd_device_name(kind: str) -> str:
    """Return the default sounddevice device name for kind in {'input',
    'output'}. Empty string on any failure (sounddevice missing, no
    default device, query raises). Shared by Windows + macOS backends."""
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except Exception:
        return ""
    try:
        d = sd.query_devices(kind=kind)
    except Exception:
        return ""
    if isinstance(d, dict):
        return str(d.get("name") or "").strip()
    return ""


def _windows_default_input() -> str:
    return _sd_device_name("input")


def _windows_default_output() -> str:
    return _sd_device_name("output")


# ---------- macOS backend (CoreAudio via sounddevice) -------------------

def _macos_default_input() -> str:
    return _sd_device_name("input")


def _macos_default_output() -> str:
    return _sd_device_name("output")


# ---------- Public dispatch surface ------------------------------------

def default_input_source_name() -> str:
    """Cross-platform default input (mic) device name. Empty on failure."""
    sysname = platform.system()
    if sysname == "Linux":
        return _linux_default_source()
    if sysname == "Windows":
        return _windows_default_input()
    if sysname == "Darwin":
        return _macos_default_input()
    return ""


def default_output_sink_name() -> str:
    """Cross-platform default output (speaker/headphone) device name.

    Linux returns the pw-dump free-text descriptor (sink description +
    form-factor), with a pactl fallback for PulseAudio-only systems —
    that's the same shape output_profile's classifier consumes.

    Windows/macOS return the sounddevice default-output device name.
    Empty on any failure.
    """
    sysname = platform.system()
    if sysname == "Linux":
        desc = _linux_pwdump_active_sink_desc()
        if desc.strip():
            return desc
        # Fallback: PulseAudio-only Linux hosts.
        block = _linux_pactl_active_sink_block()
        return block
    if sysname == "Windows":
        return _windows_default_output()
    if sysname == "Darwin":
        return _macos_default_output()
    return ""


def l1_echo_cancel_active() -> bool:
    """True iff the OS-level echo cancellation is active in the mic path.

    Linux:   PipeWire module-echo-cancel — detected when the default
             capture source's name contains "echo-cancel".
    Windows: Always False. WASAPI has no PipeWire-equivalent virtual
             echo-cancel source; the cascade falls through to L2 (APM)
             and L3 (DTLN), both cross-platform.
    macOS:   Always False. Same rationale as Windows.
    Other:   Always False (graceful degrade).
    """
    if platform.system() != "Linux":
        return False
    return "echo-cancel" in _linux_default_source().lower()

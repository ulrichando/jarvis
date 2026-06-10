"""OSV package vulnerability scanner for JARVIS voice-agent.

Wraps the Open Source Vulnerabilities (OSV.dev) public API to let the
supervisor check packages before recommending `pip install` / `npm install`
commands or before the terminal tool runs `npx`/`uvx` installs.

Registered tool name: ``vuln_check``

No credentials required — the OSV API is free and public.
Fail-open: network errors return a clean result so the user is never
blocked from installing a legitimate package due to a transient outage.

Faithful port of the upstream ``osv_check`` library, exposed here as a
JARVIS-native registered tool. No upstream brand tokens.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Optional, Tuple

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

_OSV_ENDPOINT = os.getenv("OSV_ENDPOINT", "https://api.osv.dev/v1/query")
_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Internal helpers (ported from upstream osv_check.py, renamed to JARVIS)
# ---------------------------------------------------------------------------

def _infer_ecosystem(command: str) -> Optional[str]:
    """Infer package ecosystem from the command name."""
    base = os.path.basename(command).lower()
    if base in {"npx", "npx.cmd"}:
        return "npm"
    if base in {"uvx", "uvx.cmd", "pipx", "pip"}:
        return "PyPI"
    return None


# OSV is case-sensitive on the ecosystem name ("pypi" → HTTP 400 "Invalid
# ecosystem"; "PyPI" → 200). The explicit ``ecosystem`` arg is LLM/user-typed,
# so map the common spellings to OSV's canonical casing before querying.
# Without this a trivial casing slip makes the OSV call 400, which the
# fail-open path then masks as "safe: true" — a security tool silently not
# checking. (Live-verify finding 2026-06.)
_OSV_ECOSYSTEM_ALIASES = {
    "pypi": "PyPI", "python": "PyPI", "pip": "PyPI",
    "npm": "npm", "node": "npm", "nodejs": "npm",
    "go": "Go", "golang": "Go",
    "maven": "Maven", "java": "Maven",
    "nuget": "NuGet", "dotnet": "NuGet", ".net": "NuGet",
    "rubygems": "RubyGems", "gem": "RubyGems", "ruby": "RubyGems",
    "crates.io": "crates.io", "crates": "crates.io", "cargo": "crates.io", "rust": "crates.io",
    "packagist": "Packagist", "composer": "Packagist", "php": "Packagist",
    "hex": "Hex", "elixir": "Hex",
    "pub": "Pub", "dart": "Pub",
}


def _canonical_ecosystem(ecosystem: str) -> str:
    """Map a user/LLM-supplied ecosystem string to OSV's canonical casing.

    Unknown values pass through unchanged so a valid-but-unlisted OSV
    ecosystem (e.g. "Alpine", "Debian:12") still works; only the common
    lowercase spellings are corrected.
    """
    return _OSV_ECOSYSTEM_ALIASES.get((ecosystem or "").strip().lower(), ecosystem)


def _parse_npm_package(token: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse npm package: @scope/name@version or name@version."""
    if token.startswith("@"):
        match = re.match(r"^(@[^/]+/[^@]+)(?:@(.+))?$", token)
        if match:
            return match.group(1), match.group(2)
        return token, None
    if "@" in token:
        parts = token.rsplit("@", 1)
        name = parts[0]
        version = parts[1] if len(parts) > 1 and parts[1] != "latest" else None
        return name, version
    return token, None


def _parse_pypi_package(token: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse PyPI package: name==version or name[extras]==version."""
    match = re.match(r"^([a-zA-Z0-9._-]+)(?:\[[^\]]*\])?(?:==(.+))?$", token)
    if match:
        return match.group(1), match.group(2)
    return token, None


def _query_osv(package: str, ecosystem: str, version: Optional[str] = None) -> list:
    """Query the OSV API. Returns list of matching vulnerability records."""
    payload = {"package": {"name": package, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _OSV_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "jarvis-voice-agent-vuln-check/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        result = json.loads(resp.read())
    return result.get("vulns", [])


def _osv_fail_open(package: str, ecosystem: str, version: Optional[str], exc: Exception) -> dict:
    """Transient-outage fallback: proceed without a check rather than block."""
    return {
        "safe": True,
        "package": package,
        "ecosystem": ecosystem,
        "version": version,
        "vuln_count": 0,
        "malware_count": 0,
        "vulns": [],
        "note": f"OSV API unreachable ({exc}); proceeding without security check.",
    }


def _check_package_for_vulns(
    package: str,
    ecosystem: str,
    version: Optional[str] = None,
    *,
    malware_only: bool = False,
) -> dict:
    """Check a package for vulnerabilities via OSV.

    Returns a dict with keys:
      ``safe`` (bool), ``package``, ``ecosystem``, ``version``,
      ``vuln_count``, ``malware_count``, ``vulns`` (list of dicts).

    Fail-open: returns ``{"safe": True, "error": "<msg>"}`` on network errors
    so callers are never blocked by a transient outage. A 4xx (bad request —
    e.g. an unrecognized ecosystem) is NOT a transient outage: it fails CLOSED
    (``safe: False``) so a malformed query can't masquerade as a clean bill of
    health.
    """
    try:
        vulns = _query_osv(package, ecosystem, version)
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            logger.warning(
                "OSV rejected the query for %s/%s (HTTP %s) — failing closed",
                ecosystem, package, exc.code,
            )
            return {
                "safe": False,
                "package": package,
                "ecosystem": ecosystem,
                "version": version,
                "vuln_count": 0,
                "malware_count": 0,
                "vulns": [],
                "error": (
                    f"OSV rejected the query (HTTP {exc.code}) — likely an "
                    f"unrecognized ecosystem {ecosystem!r}. Could NOT verify "
                    "this package; treat as unverified, not safe."
                ),
            }
        # 5xx / other HTTPError → transient; fall through to fail-open.
        logger.debug("OSV query failed for %s/%s (fail-open): %s", ecosystem, package, exc)
        return _osv_fail_open(package, ecosystem, version, exc)
    except Exception as exc:
        logger.debug("OSV query failed for %s/%s (fail-open): %s", ecosystem, package, exc)
        return _osv_fail_open(package, ecosystem, version, exc)

    malware = [v for v in vulns if v.get("id", "").startswith("MAL-")]
    non_malware = [v for v in vulns if not v.get("id", "").startswith("MAL-")]

    relevant = malware if malware_only else vulns

    return {
        "safe": len(malware) == 0 if malware_only else len(vulns) == 0,
        "package": package,
        "ecosystem": ecosystem,
        "version": version,
        "vuln_count": len(vulns),
        "malware_count": len(malware),
        "vulns": [
            {
                "id": v.get("id", ""),
                "summary": (v.get("summary") or "")[:200],
                "severity": (v.get("database_specific") or {}).get("severity"),
                "is_malware": v.get("id", "").startswith("MAL-"),
            }
            for v in relevant[:10]  # cap output for context window safety
        ],
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_vuln_check(args: dict) -> str:
    """Handle a vuln_check tool call.

    Supports two calling modes:
    1. Provide ``package`` + ``ecosystem`` (and optional ``version``).
    2. Provide ``command`` + ``args_list`` — the tool infers ecosystem from the
       command name (npx → npm, pip/uvx/pipx → PyPI).
    """
    package: Optional[str] = args.get("package")
    ecosystem: Optional[str] = args.get("ecosystem")
    version: Optional[str] = args.get("version")
    malware_only: bool = bool(args.get("malware_only", False))

    # Mode 2: infer from command + args
    if not package:
        command = args.get("command", "")
        args_list = args.get("args_list") or []
        if not command:
            return tool_error("Provide either 'package'+'ecosystem' or 'command'+'args_list'.")
        inferred_eco = _infer_ecosystem(command)
        if not inferred_eco:
            return tool_error(
                f"Cannot infer ecosystem from command {command!r}. "
                "Known: npx (npm), pip/uvx/pipx (PyPI). "
                "Pass 'package' and 'ecosystem' directly instead."
            )
        if not args_list:
            return tool_error("args_list is required when using command mode.")
        token = next((a for a in args_list if isinstance(a, str) and not a.startswith("-")), None)
        if not token:
            return tool_error("No package token found in args_list.")
        if inferred_eco == "npm":
            package, version = _parse_npm_package(token)
        else:
            package, version = _parse_pypi_package(token)
        ecosystem = inferred_eco

    if not package:
        return tool_error("'package' is required.")
    if not ecosystem:
        return tool_error("'ecosystem' is required (e.g. 'PyPI', 'npm', 'Go', 'RubyGems').")

    # Normalize casing so a "pypi"/"python" slip doesn't 400 the OSV call and
    # then fail open as "safe".
    ecosystem = _canonical_ecosystem(ecosystem)

    result = _check_package_for_vulns(package, ecosystem, version, malware_only=malware_only)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_VULN_CHECK_SCHEMA = {
    "name": "vuln_check",
    "description": (
        "Check a package for known vulnerabilities or malware via the OSV.dev "
        "public API. Use before recommending package installs to the user, "
        "or when asked if a package is safe. "
        "Two calling modes:\n"
        "1. Direct: provide package + ecosystem (+ optional version).\n"
        "2. Command: provide command + args_list — infers ecosystem from command "
        "(npx → npm, pip/uvx/pipx → PyPI). "
        "Set malware_only=true to check only confirmed MAL-* advisories and "
        "ignore regular CVEs. "
        "Fail-open: network errors return safe=true so the user is never blocked "
        "by a transient API outage."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {
                "type": "string",
                "description": "Package name to check (e.g. 'requests', '@angular/core').",
            },
            "ecosystem": {
                "type": "string",
                "description": (
                    "Package ecosystem. Common values: 'PyPI', 'npm', 'Go', "
                    "'crates.io', 'RubyGems', 'Maven', 'NuGet', 'Hex', 'Pub'."
                ),
            },
            "version": {
                "type": "string",
                "description": (
                    "Optional specific version to check (e.g. '2.28.1'). "
                    "Omit to check for any vulnerability across all versions."
                ),
            },
            "command": {
                "type": "string",
                "description": (
                    "Command-mode: the installer command name (e.g. 'npx', 'pip', 'uvx'). "
                    "Ecosystem is inferred automatically. Requires args_list."
                ),
            },
            "args_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Command-mode: the command arguments (e.g. ['@modelcontextprotocol/server-everything']). "
                    "The first non-flag token is treated as the package name."
                ),
            },
            "malware_only": {
                "type": "boolean",
                "description": (
                    "When true, only report confirmed malware advisories (MAL-* IDs) "
                    "and ignore regular CVEs. Useful for pre-install safety checks."
                ),
            },
        },
        "required": [],
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="vuln_check",
    schema=_VULN_CHECK_SCHEMA,
    handler=_handle_vuln_check,
    description=_VULN_CHECK_SCHEMA["description"],
    emoji="",
)

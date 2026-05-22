"""Tests for tools.url_safety — the SSRF guard for web_fetch.

Design contract:
  - Block: non-http(s) schemes; hosts resolving to private/loopback IPs.
  - Allow: legitimate public https URLs.
  - Env bypass: JARVIS_WEB_ALLOW_PRIVATE=1 forces all results to None.
  - Integration: a private URL through _handle_web_fetch returns a denial
    string (no actual HTTP request made).

DNS resolution is avoided in tests that need IP-level precision by using
literal IP addresses in URLs — no real DNS calls are made for those cases.
Tests that exercise the hostname→DNS path use monkeypatching.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check(url: str):
    from tools.url_safety import check_url
    return check_url(url)


def _blocks(url: str):
    result = _check(url)
    assert result is not None, f"Expected BLOCK but got ALLOW for: {url!r}"
    assert result.startswith("Error:"), f"Denial should start with 'Error:' for: {url!r}"
    assert "JARVIS_WEB_ALLOW_PRIVATE" in result, (
        f"Bypass env var not mentioned in denial: {result!r}"
    )


def _allows(url: str):
    result = _check(url)
    assert result is None, (
        f"Expected ALLOW but got BLOCK for: {url!r}\nDenial: {result}"
    )


# ---------------------------------------------------------------------------
# Scheme blocking — these never need DNS resolution
# ---------------------------------------------------------------------------


def test_blocks_file_scheme():
    _blocks("file:///etc/passwd")


def test_blocks_file_scheme_shadow():
    _blocks("file:///etc/shadow")


def test_blocks_gopher_scheme():
    _blocks("gopher://internal.corp/resource")


def test_blocks_ftp_scheme():
    _blocks("ftp://files.example.com/pub")


def test_blocks_data_scheme():
    _blocks("data:text/html,<script>alert(1)</script>")


# ---------------------------------------------------------------------------
# Loopback / private literal IPs — no DNS needed
# ---------------------------------------------------------------------------


def test_blocks_loopback_127():
    _blocks("http://127.0.0.1/api")


def test_blocks_loopback_127_with_path():
    _blocks("http://127.0.0.1/x")


def test_blocks_loopback_127_0_0_1_port():
    _blocks("http://127.0.0.1:8080/admin")


def test_blocks_loopback_localhost_via_literal():
    # We monkeypatch resolution to return 127.0.0.1 so we don't rely on /etc/hosts.
    with patch("tools.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
        _blocks("http://localhost/api")


def test_blocks_link_local_metadata_169():
    # The classic AWS/GCP/Azure instance-metadata endpoint.
    _blocks("http://169.254.169.254/latest/meta-data/")


def test_blocks_link_local_generic():
    _blocks("http://169.254.0.1/anything")


def test_blocks_private_rfc1918_10():
    _blocks("http://10.0.0.5/internal")


def test_blocks_private_rfc1918_192_168():
    _blocks("http://192.168.1.1/router")


def test_blocks_private_rfc1918_172_16():
    _blocks("http://172.16.0.1/intranet")


def test_blocks_private_rfc1918_172_31():
    _blocks("http://172.31.255.254/meta-data")


def test_blocks_private_ipv6_loopback():
    # IPv6 loopback ::1 — use a monkeypatched resolver.
    with patch("tools.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("::1", 80, 0, 0))]
        _blocks("http://[::1]/admin")


def test_blocks_private_ipv6_unique_local():
    with patch("tools.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("fd00::1", 80, 0, 0))]
        _blocks("http://myhost.local/resource")


# ---------------------------------------------------------------------------
# Hostname that resolves to private — DNS monkeypatched
# ---------------------------------------------------------------------------


def test_blocks_hostname_resolving_to_10_x(monkeypatch):
    with patch("tools.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("10.1.2.3", 443))]
        _blocks("https://internal.corp.example.com/secret")


# ---------------------------------------------------------------------------
# Allowed URLs — public https / http addresses
# ---------------------------------------------------------------------------


def test_allows_https_example_com():
    _allows("https://example.com")


def test_allows_https_duckduckgo():
    _allows("https://duckduckgo.com")


def test_allows_https_with_path():
    _allows("https://example.com/some/path?q=1")


def test_allows_http_public():
    # Ordinary http to a public address is fine (not SSRF-risky).
    _allows("http://example.com/page")


def test_allows_empty_url():
    # Empty URL — no denial (let the caller handle it).
    _allows("")


# ---------------------------------------------------------------------------
# DNS failure — treated as allow (let the fetcher produce the error)
# ---------------------------------------------------------------------------


def test_dns_failure_is_allowed():
    """When DNS resolution fails we ALLOW — the fetcher will surface the error."""
    import socket as _socket
    with patch("tools.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.side_effect = _socket.gaierror("NXDOMAIN")
        # Should not raise and should return None (allow).
        result = _check("https://nonexistent.xyzzy.invalid/")
        assert result is None, f"DNS failure should be allowed, got: {result!r}"


# ---------------------------------------------------------------------------
# Env bypass — JARVIS_WEB_ALLOW_PRIVATE=1
# ---------------------------------------------------------------------------


def test_bypass_allows_localhost(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_ALLOW_PRIVATE", "1")
    from tools.url_safety import check_url
    assert check_url("http://127.0.0.1/admin") is None


def test_bypass_allows_metadata_endpoint(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_ALLOW_PRIVATE", "1")
    from tools.url_safety import check_url
    assert check_url("http://169.254.169.254/latest/meta-data/") is None


def test_bypass_allows_file_scheme(monkeypatch):
    # File scheme is still blocked even with JARVIS_WEB_ALLOW_PRIVATE=1?
    # No — the bypass disables ALL checks (power-user opt-out). That's the
    # documented behaviour for the flag.
    monkeypatch.setenv("JARVIS_WEB_ALLOW_PRIVATE", "1")
    from tools.url_safety import check_url
    assert check_url("file:///etc/passwd") is None


def test_bypass_allows_private_rfc1918(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_ALLOW_PRIVATE", "1")
    from tools.url_safety import check_url
    assert check_url("http://192.168.1.1/") is None


# ---------------------------------------------------------------------------
# Integration — _handle_web_fetch returns denial for private URLs,
# with no actual HTTP I/O.
# ---------------------------------------------------------------------------


def _run_async(coro):
    """Run a coroutine in a fresh event loop to avoid cross-test loop state."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_web_fetch_handler_blocks_localhost():
    """_handle_web_fetch must return a denial string for http://127.0.0.1/
    without making any network request."""
    import urllib.request

    urlopen_called = []

    def _fake_urlopen(*a, **kw):
        urlopen_called.append(True)
        raise AssertionError("urlopen should not be called for blocked URLs")

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        from tools.web_tools import _handle_web_fetch

        result = _run_async(_handle_web_fetch({"url": "http://127.0.0.1/api"}))

    assert not urlopen_called, "urlopen was called despite a blocked private URL"
    assert "Error:" in result, f"Expected a denial string, got: {result!r}"
    assert "SSRF" in result or "private" in result.lower() or "refusing" in result.lower()


def test_web_fetch_handler_blocks_metadata_endpoint():
    """The AWS metadata endpoint must be blocked without any network call."""
    import urllib.request

    urlopen_called = []

    def _fake_urlopen(*a, **kw):
        urlopen_called.append(True)
        raise AssertionError("urlopen should not be called for blocked URLs")

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        from tools.web_tools import _handle_web_fetch

        result = _run_async(
            _handle_web_fetch({"url": "http://169.254.169.254/latest/meta-data"})
        )

    assert not urlopen_called
    assert "Error:" in result


def test_web_fetch_handler_blocks_file_scheme():
    """file:// URLs must be blocked before any I/O."""
    import urllib.request

    urlopen_called = []

    def _fake_urlopen(*a, **kw):
        urlopen_called.append(True)
        raise AssertionError("urlopen should not be called for blocked URLs")

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        from tools.web_tools import _handle_web_fetch

        # file:// has a scheme so it goes straight to check_url before
        # the https:// prepend path — it must be caught and blocked.
        result = _run_async(_handle_web_fetch({"url": "file:///etc/passwd"}))

    assert not urlopen_called
    assert "Error:" in result


def test_web_fetch_handler_allows_public_url():
    """A legitimate public URL reaches urlopen (or fails with a network error
    — either way it's NOT blocked at the safety layer)."""
    import urllib.request
    import urllib.error

    urlopen_called = []

    def _fake_urlopen(req, *a, **kw):
        urlopen_called.append(True)
        # Simulate an HTTP error (e.g. 404) — this is a legit network response,
        # not a safety block.
        raise urllib.error.HTTPError(
            str(req), 404, "Not Found", {}, None  # type: ignore[arg-type]
        )

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        from tools.web_tools import _handle_web_fetch

        result = _run_async(_handle_web_fetch({"url": "https://example.com/page"}))

    assert urlopen_called, (
        "urlopen was not called — public URL was incorrectly blocked"
    )
    # The result should be a network-error message, NOT a safety denial.
    assert "Error:" not in result[:6] or "could not be retrieved" in result.lower(), (
        f"Unexpected safety block for a public URL: {result!r}"
    )

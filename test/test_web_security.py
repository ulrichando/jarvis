"""Tests for web server security functions.

Covers:
- SPA fallback path traversal guard
- WebSocket origin validation (_check_ws_origin)
"""

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Extract the security logic into standalone helpers so we can test without
# instantiating the full JarvisWebServer (which pulls in Brain, edge_tts,
# numpy, etc.).
# ---------------------------------------------------------------------------

def spa_path_is_safe(static_dir: Path, request_path: str) -> bool:
    """Reproduce the SPA fallback traversal guard from web_server.py.

    Returns True when the resolved path stays inside *static_dir* **and**
    the file actually exists; False otherwise (caller should serve
    index.html).
    """
    path = (static_dir / request_path.lstrip("/")).resolve()
    static_root = static_dir.resolve()
    return str(path).startswith(str(static_root)) and path.exists() and path.is_file()


# Allowed-origins set copied verbatim from JarvisWebServer._ALLOWED_ORIGINS
_ALLOWED_ORIGINS = {
    "http://localhost", "http://127.0.0.1", "http://0.0.0.0",
    "https://localhost", "https://127.0.0.1",
}


def check_ws_origin(origin: str, host: str) -> bool:
    """Reproduce _check_ws_origin logic from web_server.py.

    Parameters
    ----------
    origin : str
        The value of the ``Origin`` HTTP header (empty string if absent).
    host : str
        The value of the ``Host`` HTTP header (empty string if absent).

    Returns True if the connection should be allowed.
    """
    if not origin:
        return True  # No origin = direct connection (curl, desktop app)
    # Strip port for comparison
    origin_base = re.sub(r':\d+$', '', origin)
    if origin_base in _ALLOWED_ORIGINS:
        return True
    # Allow same-host connections (exact match to prevent subdomain spoofing)
    if host:
        host_name = host.split(":")[0]
        origin_host = re.sub(r':\d+$', '', re.sub(r'^https?://', '', origin))
        if origin_host == host_name:
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSPAPathTraversal(unittest.TestCase):
    """Validate the SPA fallback path-traversal guard."""

    def setUp(self):
        # Use a well-known directory that definitely exists on disk
        # so we can probe resolved-path behaviour.  /tmp is safe.
        self.static_dir = Path("/tmp/jarvis_test_static")
        self.static_dir.mkdir(exist_ok=True)
        # Create some fake static assets
        (self.static_dir / "index.html").write_text("<html></html>")
        assets = self.static_dir / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "main.js").write_text("console.log('hi');")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.static_dir, ignore_errors=True)

    # -- Normal paths -------------------------------------------------------

    def test_normal_index_html(self):
        """A simple /index.html should be safe."""
        self.assertTrue(spa_path_is_safe(self.static_dir, "/index.html"))

    def test_normal_asset(self):
        """/assets/main.js should be safe."""
        self.assertTrue(spa_path_is_safe(self.static_dir, "/assets/main.js"))

    def test_nonexistent_path_falls_back(self):
        """A path that doesn't exist should return False (-> serve index.html)."""
        self.assertFalse(spa_path_is_safe(self.static_dir, "/no/such/file.js"))

    # -- Path traversal attempts --------------------------------------------

    def test_traversal_dot_dot(self):
        """/../../../etc/passwd must NOT be considered safe."""
        self.assertFalse(
            spa_path_is_safe(self.static_dir, "/../../../etc/passwd")
        )

    def test_traversal_encoded_slashes(self):
        """URL-encoded traversal like /..%2f..%2fetc/passwd should fail.

        Even though Python's Path will usually not decode %2f, the resolved
        path either stays inside STATIC_DIR (file won't exist) or escapes
        (and startswith check catches it).  Either way: not safe.
        """
        self.assertFalse(
            spa_path_is_safe(self.static_dir, "/..%2f..%2fetc/passwd")
        )

    def test_traversal_double_dot_segments(self):
        """Multiple ../../ segments should not escape."""
        self.assertFalse(
            spa_path_is_safe(self.static_dir, "/assets/../../etc/shadow")
        )

    def test_traversal_stays_inside_but_no_file(self):
        """Traversal that resolves back inside static dir but file is missing."""
        # /assets/../nonexistent resolves to static_dir/nonexistent
        self.assertFalse(
            spa_path_is_safe(self.static_dir, "/assets/../nonexistent")
        )

    def test_traversal_resolves_back_inside_existing(self):
        """If traversal resolves back to a valid file inside static dir, allow it.

        /assets/../index.html resolves to static_dir/index.html -- that is
        inside the root and exists, so it is safe.
        """
        self.assertTrue(
            spa_path_is_safe(self.static_dir, "/assets/../index.html")
        )


class TestWSOriginValidation(unittest.TestCase):
    """Validate WebSocket origin checking logic."""

    # -- Empty / missing origin ---------------------------------------------

    def test_empty_origin_allowed(self):
        """Empty origin (direct curl/desktop) should be allowed."""
        self.assertTrue(check_ws_origin("", ""))

    def test_no_origin_with_host(self):
        """No origin but host present -> allowed."""
        self.assertTrue(check_ws_origin("", "localhost:8765"))

    # -- Allowed origins ----------------------------------------------------

    def test_localhost_http(self):
        self.assertTrue(check_ws_origin("http://localhost", ""))

    def test_localhost_http_with_port(self):
        self.assertTrue(check_ws_origin("http://localhost:8765", ""))

    def test_127_http(self):
        self.assertTrue(check_ws_origin("http://127.0.0.1", ""))

    def test_127_http_with_port(self):
        self.assertTrue(check_ws_origin("http://127.0.0.1:3000", ""))

    def test_0000_http(self):
        self.assertTrue(check_ws_origin("http://0.0.0.0", ""))

    def test_localhost_https(self):
        self.assertTrue(check_ws_origin("https://localhost", ""))

    def test_127_https(self):
        self.assertTrue(check_ws_origin("https://127.0.0.1", ""))

    # -- Spoofing / malicious origins ---------------------------------------

    def test_evil_dot_com_rejected(self):
        """Plainly evil origin must be rejected."""
        self.assertFalse(check_ws_origin("http://evil.com", ""))

    def test_evil_com_with_localhost_suffix(self):
        """http://evil.com127.0.0.1 must NOT match the allowed list."""
        self.assertFalse(check_ws_origin("http://evil.com127.0.0.1", ""))

    def test_evil_com_localhost_subdomain(self):
        """http://localhost.evil.com must be rejected."""
        self.assertFalse(check_ws_origin("http://localhost.evil.com", ""))

    def test_evil_com_with_port(self):
        self.assertFalse(check_ws_origin("http://evil.com:8765", ""))

    def test_attacker_origin_with_path(self):
        """Origin with extra path component should be rejected."""
        self.assertFalse(check_ws_origin("http://evil.com/http://localhost", ""))

    # -- Host-header matching -----------------------------------------------

    def test_matching_host_header(self):
        """When origin host matches Host header exactly, allow."""
        self.assertTrue(
            check_ws_origin("http://myserver.local:8765", "myserver.local:8765")
        )

    def test_matching_host_no_port(self):
        """Origin without port, Host without port -- should match."""
        self.assertTrue(
            check_ws_origin("http://myserver.local", "myserver.local")
        )

    def test_host_mismatch_rejected(self):
        """Origin host != Host header -> reject."""
        self.assertFalse(
            check_ws_origin("http://attacker.com", "myserver.local:8765")
        )

    def test_subdomain_spoof_via_host(self):
        """Origin=http://evil.myserver.local with Host=myserver.local -> reject.

        The exact-match check should prevent subdomain tricks.
        """
        self.assertFalse(
            check_ws_origin("http://evil.myserver.local", "myserver.local")
        )


if __name__ == "__main__":
    unittest.main()

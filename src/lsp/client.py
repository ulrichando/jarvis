"""JARVIS LSP Client — JSON-RPC stdio transport for language servers.

Ported from claw-code's Rust LSP crate. Communicates with language
servers using Content-Length framed JSON-RPC over stdin/stdout.
"""

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.lsp.client")


@dataclass
class LspClient:
    """LSP client that communicates with a language server via stdio JSON-RPC."""

    _process: subprocess.Popen | None = field(default=None, repr=False)
    _request_id: int = field(default=0, repr=False)
    _id_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pending: dict[int, threading.Event] = field(default_factory=dict, repr=False)
    _responses: dict[int, dict] = field(default_factory=dict, repr=False)
    _diagnostics: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    _open_documents: dict[str, int] = field(default_factory=dict, repr=False)
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _shutdown: bool = field(default=False, repr=False)
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── lifecycle ──────────────────────────────────────────────

    def connect(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        workspace_root: str = ".",
    ) -> dict:
        """Spawn the language server and send the initialize request."""
        cmd = [command] + (args or [])
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Start background reader
        self._reader_thread = threading.Thread(
            target=self._background_reader, daemon=True
        )
        self._reader_thread.start()

        # Send initialize
        result = self._send_request(
            "initialize",
            {
                "processId": None,
                "rootUri": f"file://{workspace_root}",
                "capabilities": {
                    "textDocument": {
                        "synchronization": {"didOpen": True, "didClose": True},
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "publishDiagnostics": {"relatedInformation": True},
                    }
                },
            },
        )

        # Send initialized notification
        self._send_notification("initialized", {})
        return result

    def shutdown(self):
        """Send shutdown request then exit notification."""
        if self._shutdown or self._process is None:
            return
        self._shutdown = True
        try:
            self._send_request("shutdown", None)
        except Exception:
            pass
        try:
            self._send_notification("exit", None)
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            if self._process:
                self._process.kill()

    # ── document operations ────────────────────────────────────

    def open_document(self, path: str):
        """Send textDocument/didOpen notification."""
        uri = _path_to_uri(path)
        version = self._open_documents.get(uri, 0) + 1
        self._open_documents[uri] = version

        try:
            text = _read_file(path)
        except OSError as exc:
            log.warning("Cannot read %s: %s", path, exc)
            return

        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": _guess_language(path),
                    "version": version,
                    "text": text,
                }
            },
        )

    def close_document(self, path: str):
        """Send textDocument/didClose notification."""
        uri = _path_to_uri(path)
        self._open_documents.pop(uri, None)
        self._send_notification(
            "textDocument/didClose", {"textDocument": {"uri": uri}}
        )

    # ── queries ────────────────────────────────────────────────

    def go_to_definition(
        self, path: str, line: int, character: int
    ) -> list[dict]:
        """Send textDocument/definition and return location(s)."""
        result = self._send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": character},
            },
        )
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return result
        return []

    def find_references(
        self, path: str, line: int, character: int
    ) -> list[dict]:
        """Send textDocument/references and return location(s)."""
        result = self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            },
        )
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return [result]

    def get_diagnostics(self) -> dict[str, list[dict]]:
        """Return cached diagnostics from publishDiagnostics notifications."""
        return dict(self._diagnostics)

    # ── JSON-RPC transport ─────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            self._request_id += 1
            return self._request_id

    def _send_request(self, method: str, params: dict | None) -> dict | None:
        """Send a JSON-RPC request and wait for response."""
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params

        event = threading.Event()
        self._pending[rid] = event
        self._write_message(msg)

        if not event.wait(timeout=30):
            self._pending.pop(rid, None)
            log.warning("LSP request %s (id=%d) timed out", method, rid)
            return None

        self._pending.pop(rid, None)
        response = self._responses.pop(rid, {})
        if "error" in response:
            log.warning("LSP error for %s: %s", method, response["error"])
            return None
        return response.get("result")

    def _send_notification(self, method: str, params: dict | None):
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write_message(msg)

    def _write_message(self, msg: dict):
        """Write a Content-Length framed JSON message to stdin."""
        if self._process is None or self._process.stdin is None:
            return
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            self._process.stdin.write(header + body)
            self._process.stdin.flush()

    def _read_message(self) -> dict | None:
        """Read a Content-Length framed JSON message from stdout."""
        stdout = self._process.stdout
        if stdout is None:
            return None

        # Read headers
        content_length = 0
        while True:
            line = stdout.readline()
            if not line:
                return None
            line = line.decode("ascii", errors="replace").strip()
            if not line:
                break  # End of headers
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())

        if content_length <= 0:
            return None

        body = stdout.read(content_length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    # ── background reader ──────────────────────────────────────

    def _background_reader(self):
        """Background thread that reads messages from the server."""
        while not self._shutdown:
            try:
                msg = self._read_message()
            except Exception as exc:
                if not self._shutdown:
                    log.debug("LSP reader error: %s", exc)
                break

            if msg is None:
                break

            # Response to a request
            if "id" in msg and ("result" in msg or "error" in msg):
                rid = msg["id"]
                self._responses[rid] = msg
                event = self._pending.get(rid)
                if event:
                    event.set()

            # Server notification
            elif msg.get("method") == "textDocument/publishDiagnostics":
                params = msg.get("params", {})
                uri = params.get("uri", "")
                self._diagnostics[uri] = params.get("diagnostics", [])

            # Server request (e.g. window/workDoneProgress/create) — just ack
            elif "id" in msg and "method" in msg:
                self._write_message(
                    {"jsonrpc": "2.0", "id": msg["id"], "result": None}
                )


# ── helpers ────────────────────────────────────────────────────

def _path_to_uri(path: str) -> str:
    """Convert a filesystem path to a file:// URI."""
    import os
    abspath = os.path.abspath(path)
    return f"file://{abspath}"


def _read_file(path: str) -> str:
    """Read a file's contents as UTF-8."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


_LANG_MAP = {
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".lua": "lua",
    ".sh": "shellscript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
}


def _guess_language(path: str) -> str:
    """Guess the LSP language ID from file extension."""
    import os
    _, ext = os.path.splitext(path)
    return _LANG_MAP.get(ext.lower(), "plaintext")

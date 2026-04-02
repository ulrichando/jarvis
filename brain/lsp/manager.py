"""JARVIS LSP Manager — multi-server routing and context enrichment.

Ported from claw-code's Rust LSP manager. Routes file operations to
the correct language server based on file extension and aggregates
diagnostics/definitions for prompt enrichment.
"""

import os
import logging
from dataclasses import dataclass, field

from brain.lsp.client import LspClient

log = logging.getLogger("jarvis.lsp.manager")


# ── data classes ───────────────────────────────────────────────


@dataclass
class LspServerConfig:
    """Configuration for a single language server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)  # e.g. [".py", ".pyi"]
    workspace_root: str = "."


@dataclass
class SymbolLocation:
    """A location returned by go-to-definition or find-references."""

    path: str
    line: int
    character: int


@dataclass
class FileDiagnostics:
    """Diagnostics for a single file."""

    path: str
    diagnostics: list[dict] = field(default_factory=list)


@dataclass
class LspContext:
    """Aggregated LSP context for prompt enrichment."""

    diagnostics: list[FileDiagnostics] = field(default_factory=list)
    definitions: list[SymbolLocation] = field(default_factory=list)
    references: list[SymbolLocation] = field(default_factory=list)

    def render_for_prompt(self, max_chars: int = 4000) -> str:
        """Render context as a text block, capped to max_chars."""
        parts: list[str] = []

        if self.diagnostics:
            parts.append("## Diagnostics")
            for fd in self.diagnostics:
                for diag in fd.diagnostics:
                    severity = _severity_label(diag.get("severity", 1))
                    msg = diag.get("message", "")
                    rng = diag.get("range", {}).get("start", {})
                    line = rng.get("line", 0)
                    parts.append(f"  {severity} {fd.path}:{line}: {msg}")

        if self.definitions:
            parts.append("## Definitions")
            for loc in self.definitions:
                parts.append(f"  {loc.path}:{loc.line}:{loc.character}")

        if self.references:
            parts.append("## References")
            for loc in self.references:
                parts.append(f"  {loc.path}:{loc.line}:{loc.character}")

        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text


# ── manager ────────────────────────────────────────────────────


class LspManager:
    """Routes LSP operations to the correct language server.

    Servers are lazily initialized: a server process is only spawned
    the first time a file with a matching extension is opened.
    """

    def __init__(self, configs: list[LspServerConfig] | None = None):
        self._configs: list[LspServerConfig] = configs or []
        self._clients: dict[str, LspClient] = {}  # config.name -> client
        self._ext_map: dict[str, str] = {}  # ".py" -> config.name

        for cfg in self._configs:
            for ext in cfg.extensions:
                self._ext_map[ext.lower()] = cfg.name

    # ── routing ────────────────────────────────────────────────

    def _config_for(self, name: str) -> LspServerConfig | None:
        for cfg in self._configs:
            if cfg.name == name:
                return cfg
        return None

    def _server_for_path(self, path: str) -> str | None:
        """Return the server name that handles this file extension."""
        _, ext = os.path.splitext(path)
        return self._ext_map.get(ext.lower())

    def _ensure_client(self, server_name: str) -> LspClient | None:
        """Lazily start the language server if not running."""
        if server_name in self._clients:
            return self._clients[server_name]

        cfg = self._config_for(server_name)
        if cfg is None:
            return None

        client = LspClient()
        try:
            client.connect(
                command=cfg.command,
                args=cfg.args,
                workspace_root=cfg.workspace_root,
            )
            self._clients[server_name] = client
            log.info("Started LSP server: %s (%s)", cfg.name, cfg.command)
            return client
        except Exception as exc:
            log.error("Failed to start LSP server %s: %s", cfg.name, exc)
            return None

    def _client_for_path(self, path: str) -> LspClient | None:
        """Get (or lazily start) the client for a file path."""
        name = self._server_for_path(path)
        if name is None:
            return None
        return self._ensure_client(name)

    # ── public API ─────────────────────────────────────────────

    def supports_path(self, path: str) -> bool:
        """Check if any configured server handles this file extension."""
        return self._server_for_path(path) is not None

    def open_document(self, path: str):
        """Open a document in the appropriate language server."""
        client = self._client_for_path(path)
        if client:
            client.open_document(path)

    def close_document(self, path: str):
        """Close a document in the appropriate language server."""
        client = self._client_for_path(path)
        if client:
            client.close_document(path)

    def go_to_definition(
        self, path: str, line: int, character: int
    ) -> list[SymbolLocation]:
        """Go-to-definition, routed to the correct server."""
        client = self._client_for_path(path)
        if not client:
            return []
        raw = client.go_to_definition(path, line, character)
        return _parse_locations(raw)

    def find_references(
        self, path: str, line: int, character: int
    ) -> list[SymbolLocation]:
        """Find references, routed to the correct server."""
        client = self._client_for_path(path)
        if not client:
            return []
        raw = client.find_references(path, line, character)
        return _parse_locations(raw)

    def collect_diagnostics(self) -> list[FileDiagnostics]:
        """Aggregate diagnostics from all running servers."""
        all_diags: list[FileDiagnostics] = []
        for client in self._clients.values():
            for uri, diags in client.get_diagnostics().items():
                path = _uri_to_path(uri)
                if diags:
                    all_diags.append(FileDiagnostics(path=path, diagnostics=diags))
        return all_diags

    def context_for_prompt(
        self, path: str, line: int, character: int
    ) -> LspContext:
        """Build rich LSP context for prompt enrichment."""
        ctx = LspContext()
        ctx.diagnostics = self.collect_diagnostics()
        ctx.definitions = self.go_to_definition(path, line, character)
        ctx.references = self.find_references(path, line, character)
        return ctx

    def shutdown_all(self):
        """Shutdown all running language servers."""
        for name, client in self._clients.items():
            try:
                client.shutdown()
                log.info("Shut down LSP server: %s", name)
            except Exception as exc:
                log.warning("Error shutting down %s: %s", name, exc)
        self._clients.clear()


# ── helpers ────────────────────────────────────────────────────


def _parse_locations(raw: list[dict]) -> list[SymbolLocation]:
    """Parse LSP Location objects into SymbolLocation."""
    locations: list[SymbolLocation] = []
    for loc in raw:
        uri = loc.get("uri", "")
        rng = loc.get("range", {}).get("start", {})
        locations.append(
            SymbolLocation(
                path=_uri_to_path(uri),
                line=rng.get("line", 0),
                character=rng.get("character", 0),
            )
        )
    return locations


def _uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a filesystem path."""
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def _severity_label(severity: int) -> str:
    """Map LSP DiagnosticSeverity to a label."""
    return {1: "ERROR", 2: "WARN", 3: "INFO", 4: "HINT"}.get(severity, "UNKNOWN")

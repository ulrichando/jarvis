#!/usr/bin/env python3
"""JARVIS Deploy MCP Server — CI/CD and infrastructure tools for JARVIS.

Exposes tools over MCP stdio so JARVIS can manage his own deployment:

  set_github_secret    — set/update a GitHub repo secret via API
  trigger_workflow     — manually trigger or re-run a GitHub Actions workflow
  workflow_status      — check the status of a workflow run
  list_workflow_runs   — list recent runs for a workflow
  proxmox_deploy       — SSH into Proxmox and run deploy commands

Setup
─────
  Add to ~/.jarvis/mcp.json under "mcpServers":
    "jarvis-deploy": {
      "command": "python3",
      "args": ["/path/to/src/mcp/servers/jarvis_deploy.py"],
      "env": {
        "GH_TOKEN": "${GH_TOKEN}",
        "PROXMOX_HOST": "${PROXMOX_HOST}",
        "PROXMOX_USER": "${PROXMOX_USER}",
        "PROXMOX_SSH_KEY_PATH": "${PROXMOX_SSH_KEY_PATH}"
      },
      "enabled": true
    }

  Env vars (set in ~/.jarvis/.env or ~/.jarvis/.env.mcp):
    GH_TOKEN              — GitHub personal access token (needs repo + workflow scope)
    PROXMOX_HOST          — IP or hostname of Proxmox server
    PROXMOX_USER          — SSH username (e.g. root)
    PROXMOX_SSH_KEY_PATH  — path to private key (e.g. ~/.ssh/proxmox_id_ed25519)

Protocol: MCP JSON-RPC 2.0 over stdio.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from typing import Any

# ── Env ───────────────────────────────────────────────────────────────────────
GH_TOKEN            = os.environ.get("GH_TOKEN", "")
PROXMOX_HOST        = os.environ.get("PROXMOX_HOST", "")
PROXMOX_USER        = os.environ.get("PROXMOX_USER", "root")
PROXMOX_SSH_KEY     = os.environ.get("PROXMOX_SSH_KEY_PATH",
                       os.path.expanduser("~/.ssh/proxmox_id_ed25519"))
DEFAULT_REPO        = os.environ.get("JARVIS_GITHUB_REPO", "ulrichando/jarvis")

# ── MCP boilerplate ───────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "set_github_secret",
        "description": (
            "Create or update a GitHub repository secret. "
            "Use this to set PROXMOX_HOST, PROXMOX_USER, or PROXMOX_SSH_KEY "
            "so the deploy workflow can SSH into Proxmox."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "secret_name":  {"type": "string", "description": "Secret name (e.g. PROXMOX_HOST)"},
                "secret_value": {"type": "string", "description": "Secret value"},
                "repo":         {"type": "string", "description": "owner/repo", "default": DEFAULT_REPO},
            },
            "required": ["secret_name", "secret_value"],
        },
    },
    {
        "name": "trigger_workflow",
        "description": (
            "Manually trigger a GitHub Actions workflow run, or re-run the latest "
            "failed run. Use this to kick off the 'Build & Deploy JARVIS' workflow "
            "after setting secrets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow":   {"type": "string", "description": "Workflow filename or ID (e.g. deploy.yml)"},
                "ref":        {"type": "string", "description": "Branch or tag", "default": "master"},
                "repo":       {"type": "string", "description": "owner/repo", "default": DEFAULT_REPO},
                "rerun_failed": {
                    "type": "boolean",
                    "description": "If true, re-run the latest failed run instead of dispatching new",
                    "default": False,
                },
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "workflow_status",
        "description": "Get the status of recent GitHub Actions workflow runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow filename (e.g. deploy.yml)"},
                "repo":     {"type": "string", "description": "owner/repo", "default": DEFAULT_REPO},
                "limit":    {"type": "integer", "description": "Max runs to show", "default": 5},
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "list_workflow_runs",
        "description": "List all recent workflow runs across all workflows in the repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo":   {"type": "string", "description": "owner/repo", "default": DEFAULT_REPO},
                "limit":  {"type": "integer", "description": "Max runs to show", "default": 10},
                "status": {
                    "type": "string",
                    "enum": ["queued", "in_progress", "completed", "failure", "success", "all"],
                    "default": "all",
                },
            },
            "required": [],
        },
    },
    {
        "name": "proxmox_deploy",
        "description": (
            "SSH into the Proxmox server and run deployment commands. "
            "Default command pulls the latest JARVIS image and restarts the container."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "string",
                    "description": "Shell commands to run on Proxmox (semicolon-separated)",
                    "default": (
                        "cd /opt/jarvis && "
                        "docker compose pull jarvis && "
                        "docker compose up -d jarvis && "
                        "docker image prune -f && "
                        "docker inspect jarvis-jarvis-1 --format '{{.Image}}'"
                    ),
                },
                "host":     {"type": "string", "description": "Override Proxmox host"},
                "user":     {"type": "string", "description": "Override SSH user"},
                "key_path": {"type": "string", "description": "Override SSH key path"},
            },
            "required": [],
        },
    },
]


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _gh_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated GitHub API call. Raises on HTTP error."""
    if not GH_TOKEN:
        raise RuntimeError(
            "GH_TOKEN not set. Add it to ~/.jarvis/.env.mcp or ~/.jarvis/.env"
        )
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "jarvis-deploy-mcp/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} → HTTP {e.code}: {body_txt}")


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value with the repo's public key (libsodium sealed box).

    Requires PyNaCl. Falls back to a helpful error if not installed.
    """
    try:
        from nacl import encoding, public  # type: ignore
    except ImportError:
        raise RuntimeError(
            "PyNaCl not found. Reinstall JARVIS: pip install -e ."
        )
    pk_bytes = base64.b64decode(public_key_b64)
    pub_key  = public.PublicKey(pk_bytes)
    box      = public.SealedBox(pub_key)
    encrypted = box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def _set_github_secret(repo: str, name: str, value: str) -> str:
    # Get repo public key for secret encryption
    key_data = _gh_request("GET", f"/repos/{repo}/actions/secrets/public-key")
    pk       = key_data["key"]
    pk_id    = key_data["key_id"]
    encrypted = _encrypt_secret(pk, value)

    _gh_request("PUT", f"/repos/{repo}/actions/secrets/{name}", {
        "encrypted_value": encrypted,
        "key_id": pk_id,
    })
    return f"Secret '{name}' set on {repo}."


def _trigger_workflow(repo: str, workflow: str, ref: str) -> str:
    _gh_request("POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
                {"ref": ref})
    return f"Workflow '{workflow}' triggered on {repo}@{ref}."


def _rerun_failed(repo: str, workflow: str) -> str:
    runs = _gh_request(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page=5",
    )
    items = runs.get("workflow_runs", [])
    failed = [r for r in items if r.get("conclusion") in ("failure", "cancelled")]
    if not failed:
        return "No failed runs found to re-run."
    run_id = failed[0]["id"]
    _gh_request("POST", f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs")
    return (
        f"Re-running failed jobs from run #{run_id} "
        f"('{failed[0].get('display_title', '')}')."
    )


def _workflow_status(repo: str, workflow: str, limit: int) -> str:
    data = _gh_request(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page={limit}",
    )
    runs = data.get("workflow_runs", [])
    if not runs:
        return f"No runs found for workflow '{workflow}' in {repo}."

    lines = [f"Last {len(runs)} run(s) of '{workflow}' in {repo}:"]
    lines.append(f"  {'RUN ID':<12} {'STATUS':<14} {'CONCLUSION':<12} TITLE")
    lines.append("  " + "─" * 68)
    for r in runs:
        rid    = str(r.get("id", ""))
        status = r.get("status", "")
        concl  = r.get("conclusion") or "—"
        title  = (r.get("display_title") or r.get("head_commit", {}).get("message", ""))[:40]
        lines.append(f"  {rid:<12} {status:<14} {concl:<12} {title}")
    return "\n".join(lines)


def _list_runs(repo: str, limit: int, status: str) -> str:
    params = f"per_page={limit}"
    if status not in ("all", ""):
        # GitHub API uses 'status' for queued/in_progress and
        # 'conclusion' for failure/success — we filter client-side for simplicity
        pass
    data = _gh_request("GET", f"/repos/{repo}/actions/runs?{params}")
    runs = data.get("workflow_runs", [])
    if status not in ("all", ""):
        runs = [r for r in runs
                if r.get("status") == status or r.get("conclusion") == status]

    if not runs:
        return f"No runs found in {repo}."

    lines = [f"Recent workflow runs in {repo}:"]
    lines.append(f"  {'RUN ID':<12} {'WORKFLOW':<28} {'STATUS':<14} {'CONCLUSION':<12}")
    lines.append("  " + "─" * 72)
    for r in runs:
        rid  = str(r.get("id", ""))
        wf   = (r.get("name") or "")[:26]
        st   = r.get("status", "")
        co   = r.get("conclusion") or "—"
        lines.append(f"  {rid:<12} {wf:<28} {st:<14} {co:<12}")
    return "\n".join(lines)


# ── Proxmox SSH helper ────────────────────────────────────────────────────────

def _proxmox_deploy(host: str, user: str, key_path: str, commands: str) -> str:
    if not host:
        return (
            "PROXMOX_HOST is not set.\n"
            "Set it with: set_github_secret(secret_name='PROXMOX_HOST', secret_value='<your-ip>')\n"
            "And add it to ~/.jarvis/.env.mcp as: PROXMOX_HOST=<your-ip>"
        )

    key_path = os.path.expanduser(key_path)
    if not os.path.exists(key_path):
        return (
            f"SSH key not found at {key_path}.\n"
            "Set PROXMOX_SSH_KEY_PATH in ~/.jarvis/.env.mcp "
            "to the path of your private key for the Proxmox server."
        )

    ssh_cmd = [
        "ssh",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
        f"{user}@{host}",
        commands,
    ]
    try:
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=120
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0:
            return (
                f"Deploy failed (exit {result.returncode}):\n"
                + (err or out or "(no output)")
            )
        return f"Deploy successful:\n{out}" + (f"\n[stderr]\n{err}" if err else "")
    except subprocess.TimeoutExpired:
        return "SSH connection timed out after 120 seconds."
    except FileNotFoundError:
        return "ssh binary not found. Install openssh-client."
    except Exception as e:
        return f"SSH error: {e}"


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _call_tool(name: str, args: dict) -> str:
    try:
        repo = args.get("repo", DEFAULT_REPO) or DEFAULT_REPO

        if name == "set_github_secret":
            return _set_github_secret(
                repo=repo,
                name=args["secret_name"],
                value=args["secret_value"],
            )

        elif name == "trigger_workflow":
            workflow = args["workflow"]
            if args.get("rerun_failed"):
                return _rerun_failed(repo, workflow)
            return _trigger_workflow(repo, workflow, args.get("ref", "master"))

        elif name == "workflow_status":
            return _workflow_status(repo, args["workflow"], int(args.get("limit", 5)))

        elif name == "list_workflow_runs":
            return _list_runs(repo, int(args.get("limit", 10)), args.get("status", "all"))

        elif name == "proxmox_deploy":
            return _proxmox_deploy(
                host     = args.get("host", PROXMOX_HOST),
                user     = args.get("user", PROXMOX_USER),
                key_path = args.get("key_path", PROXMOX_SSH_KEY),
                commands = args.get("commands", (
                    "cd /opt/jarvis && "
                    "docker compose pull jarvis && "
                    "docker compose up -d jarvis && "
                    "docker image prune -f && "
                    "docker inspect jarvis-jarvis-1 --format '{{.Image}}'"
                )),
            )

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error in {name}: {e}"


# ── MCP JSON-RPC 2.0 stdio loop ───────────────────────────────────────────────

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        _send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "jarvis-deploy", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        })

    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})

    elif method == "tools/call":
        params   = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result_text = _call_tool(tool_name, arguments)
        _send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "content": [{"type": "text", "text": result_text}],
                "isError": result_text.startswith("Error") or "failed" in result_text.lower(),
            },
        })

    elif method == "notifications/initialized":
        pass  # No response needed for notifications

    elif req_id is not None:
        _send({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })


def main() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        try:
            _handle(msg)
        except Exception as e:
            req_id = msg.get("id")
            if req_id is not None:
                _send({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": str(e)},
                })


if __name__ == "__main__":
    main()

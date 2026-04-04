"""JARVIS Self-Packager — create minimal portable JARVIS archives.

Creates different packages for different targets:
- Full: entire JARVIS with all plugins (for Linux/Mac)
- Minimal: just the brain + web server (for constrained devices)
- Micro: CLI only, no web server (for terminals/SSH)
- Payload: single-file Python script that bootstraps everything
"""

import os
import tarfile
import tempfile
import textwrap
from pathlib import Path

JARVIS_ROOT = Path(__file__).resolve().parent.parent.parent

# Files/dirs to always exclude
EXCLUDE = {
    ".venv", "__pycache__", ".git", "target", "data",
    "*.pyc", "*.pyo", ".env", "*.db", "*.db-wal", "*.db-shm",
    "node_modules", ".restart.sh",
}


def _should_exclude(name: str) -> bool:
    base = os.path.basename(name)
    for pattern in EXCLUDE:
        if pattern.startswith("*"):
            if base.endswith(pattern[1:]):
                return True
        elif base == pattern:
            return True
    return False


def package_full(output_path: str = None) -> str:
    """Create full JARVIS archive."""
    if not output_path:
        output_path = str(Path(tempfile.gettempdir()) / "jarvis_full.tar.gz")

    with tarfile.open(output_path, "w:gz") as tar:
        for item in JARVIS_ROOT.rglob("*"):
            if item.is_file() and not any(_should_exclude(str(p)) for p in item.parts):
                arcname = str(item.relative_to(JARVIS_ROOT.parent))
                tar.add(str(item), arcname=arcname)

    size = os.path.getsize(output_path)
    return output_path


def package_minimal(output_path: str = None) -> str:
    """Create minimal package — brain + web server only."""
    if not output_path:
        output_path = str(Path(tempfile.gettempdir()) / "jarvis_minimal.tar.gz")

    include_dirs = {"brain", "shells/web", "shells/__init__.py"}

    with tarfile.open(output_path, "w:gz") as tar:
        # Core files
        for name in ["pyproject.toml", "bootstrap.sh", ".env"]:
            path = JARVIS_ROOT / name
            if path.exists():
                tar.add(str(path), arcname=f"jarvis/{name}")

        # Brain + web shell
        for root, dirs, files in os.walk(JARVIS_ROOT):
            # Filter directories
            dirs[:] = [d for d in dirs if not _should_exclude(d)]
            rel = os.path.relpath(root, JARVIS_ROOT)

            for fname in files:
                if _should_exclude(fname):
                    continue
                filepath = os.path.join(root, fname)
                arcname = f"jarvis/{rel}/{fname}"
                tar.add(filepath, arcname=arcname)

    return output_path


def generate_payload() -> str:
    """Generate a single-file Python payload that bootstraps JARVIS.

    This is a self-extracting Python script that:
    1. Downloads dependencies
    2. Creates the minimal JARVIS structure
    3. Starts the server
    """
    # Read the essential files and embed them
    essential_files = {
        "brain/__init__.py": "",
        "brain/config.py": _read_file("brain/config.py"),
        "brain/reasoning/__init__.py": "",
        "brain/reasoning/groq_client.py": _read_file("brain/reasoning/groq_client.py"),
        "brain/reasoning/persona.py": _read_file("brain/reasoning/persona.py"),
    }

    payload = textwrap.dedent('''#!/usr/bin/env python3
"""JARVIS Self-Deploying Payload — single file, bootstraps everything."""
import os, sys, subprocess, base64, json, tempfile

JARVIS_HOME = os.path.expanduser("~/.jarvis")
JARVIS_DIR = os.path.join(JARVIS_HOME, "jarvis")

def install_deps():
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "groq", "aiohttp", "rich"], check=True)

def write_files():
    os.makedirs(JARVIS_DIR, exist_ok=True)
    files = FILES_DATA
    for path, content in files.items():
        full = os.path.join(JARVIS_DIR, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

def start():
    os.chdir(JARVIS_DIR)
    sys.path.insert(0, JARVIS_DIR)
    # Start web server
    subprocess.Popen([sys.executable, "-m", "shells.web.server"],
                     cwd=JARVIS_DIR, start_new_session=True)
    print(f"JARVIS deployed at {JARVIS_DIR}")
    print("Web: http://localhost:8765")

if __name__ == "__main__":
    print("Deploying JARVIS...")
    install_deps()
    write_files()
    start()
''')

    # Embed files as a dict
    files_json = json.dumps(essential_files, indent=2)
    payload = payload.replace("FILES_DATA", f"json.loads('''{files_json}''')")

    return payload


def generate_dropper_script(target_os: str = "linux") -> str:
    """Generate a bash/powershell dropper script for a specific OS."""
    if target_os in ("linux", "macos"):
        return textwrap.dedent(f'''#!/bin/bash
# JARVIS Auto-Deployer
set -e
JARVIS_HOME="${{JARVIS_HOME:-$HOME/.jarvis}}"
mkdir -p "$JARVIS_HOME"
cd "$JARVIS_HOME"

# Download JARVIS archive from origin
if command -v curl &>/dev/null; then
    curl -sL "http://ORIGIN_IP:8765/jarvis_package.tar.gz" -o jarvis.tar.gz
elif command -v wget &>/dev/null; then
    wget -q "http://ORIGIN_IP:8765/jarvis_package.tar.gz" -O jarvis.tar.gz
fi

tar -xzf jarvis.tar.gz
cd jarvis

# Setup Python
python3 -m venv .venv 2>/dev/null || python3 -m pip install --user virtualenv && python3 -m virtualenv .venv
source .venv/bin/activate
pip install --quiet groq aiohttp rich requests beautifulsoup4 duckduckgo-search edge-tts

# Copy API key from environment or prompt
if [ -z "$GROQ_API_KEY" ]; then
    echo "GROQ_API_KEY=" > .env
else
    echo "GROQ_API_KEY=$GROQ_API_KEY" > .env
fi

# Start
nohup python3 -m shells.web.server &>/dev/null &
echo "JARVIS deployed. Web: http://$(hostname -I | awk '{{print $1}}'):8765"
''')

    elif target_os == "windows":
        return textwrap.dedent(f'''# JARVIS Auto-Deployer (PowerShell)
$ErrorActionPreference = "Stop"
$JarvisHome = "$env:USERPROFILE\\.jarvis"
New-Item -ItemType Directory -Force -Path $JarvisHome | Out-Null
Set-Location $JarvisHome

# Download
Invoke-WebRequest -Uri "http://ORIGIN_IP:8765/jarvis_package.tar.gz" -OutFile "jarvis.tar.gz"
tar -xzf jarvis.tar.gz
Set-Location jarvis

# Setup Python
python -m venv .venv
.venv\\Scripts\\activate
pip install --quiet groq aiohttp rich

# Start
Start-Process -NoNewWindow python -ArgumentList "-m shells.web.server"
Write-Host "JARVIS deployed."
''')

    elif target_os == "android":
        return textwrap.dedent(f'''#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux Deployer
pkg install -y python
pip install groq aiohttp rich
mkdir -p ~/.jarvis
cd ~/.jarvis

curl -sL "http://ORIGIN_IP:8765/jarvis_package.tar.gz" -o jarvis.tar.gz
tar -xzf jarvis.tar.gz
cd jarvis
python -m shells.web.server &
echo "JARVIS deployed on Android."
''')

    return "# Unsupported OS"


def _read_file(rel_path: str) -> str:
    path = JARVIS_ROOT / rel_path
    if path.exists():
        return path.read_text()
    return ""

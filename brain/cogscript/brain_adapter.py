"""JARVIS Brain — LLM-powered with SQLite persistent memory.

Architecture:
- Fast-paths: greetings, math, time, perception (instant, no LLM)
- Everything else: Ollama (local) → Groq (cloud fallback)
- SQLite: conversations, learned facts, user profile persist across restarts
- Voice-first: all LLM responses are clean spoken English, never code
"""

from __future__ import annotations
import sys
import time
import asyncio
import re
import random
from pathlib import Path

_jarvis_root = Path(__file__).resolve().parent.parent.parent
for p in [str(_jarvis_root)]:
    if p not in sys.path:
        sys.path.insert(0, p)


class _ReasonerCompat:
    """Duck-types brain.reasoner for the web server."""
    def __init__(self):
        self.model = "jarvis"
        self._active_model = "jarvis"

    @property
    def active_model_name(self):
        return self._active_model

    def status(self):
        return {"active_model": self.model, "providers": 0, "provider_list": []}

    class providers:
        @staticmethod
        def list_providers():
            result = []
            try:
                import requests
                r = requests.get("http://localhost:11434/api/tags", timeout=2)
                if r.status_code == 200:
                    models = r.json().get("models", [])
                    for m in models:
                        name = m.get("name", "")
                        if "embed" in name:
                            result.append({"name": f"ollama:{name}", "type": "embeddings", "status": "active"})
                        else:
                            result.append({"name": f"ollama:{name}", "type": "local", "status": "active"})
            except Exception:
                pass
            try:
                from brain.config import GROQ_API_KEY, GROQ_MODEL
                if GROQ_API_KEY:
                    result.append({"name": f"groq:{GROQ_MODEL}", "type": "cloud", "status": "active"})
            except Exception:
                pass
            return result
        @staticmethod
        def add_provider(*a, **kw):
            return None
        @staticmethod
        def remove_provider(*a, **kw):
            pass


class _AwarenessCompat:
    """Duck-types brain.awareness for the web server."""
    def __init__(self):
        self.user_energy = "neutral"
        self.user_intent = "asking"
        self.mode = "normal"
        self.vision_context = ""
        self.confidence = 0.5

    def read_user_energy(self, text: str):
        q = text.lower().strip()
        if any(w in q for w in ["wtf", "broken", "doesn't work", "fuck", "shit"]):
            self.user_energy = "frustrated"
        elif any(w in q for w in ["awesome", "perfect", "nice", "cool", "amazing"]):
            self.user_energy = "excited"
        elif len(q.split()) <= 2 and not q.endswith("?"):
            self.user_energy = "low"
        elif len(q.split()) > 15:
            self.user_energy = "high"
        else:
            self.user_energy = "neutral"

    def read_user_intent(self, text: str):
        q = text.lower().strip()
        if any(w in q for w in ["no,", "not that", "wrong", "actually", "correction", "remember that"]):
            self.user_intent = "teaching"
        elif any(w in q for w in ["what if", "how does", "why does", "explain", "tell me about"]):
            self.user_intent = "exploring"
        elif q.endswith("?") or q.startswith(("what", "where", "when", "who", "how", "is", "can", "does")):
            self.user_intent = "asking"
        else:
            self.user_intent = "commanding"


class CogScriptBrain:
    """JARVIS Brain — Ollama + Groq with SQLite memory.

    Fast-paths handle greetings, math, time instantly.
    Everything else goes to LLM with voice-friendly output.
    All conversations and learned facts persist in SQLite.
    """

    def __init__(self):
        self.reasoner = _ReasonerCompat()
        self.awareness = _AwarenessCompat()
        self._interaction_count = 0
        self.mode = "normal"  # normal, plan, agent

        # SQLite persistent memory
        from brain.memory.sqlite_memory import SQLiteMemory
        self.memory = SQLiteMemory()

        # Lightweight intelligence modules (no heavy dependencies)
        from brain.intelligence.emotional_state import EmotionalState
        from brain.intelligence.user_model import UserModel
        self._emotion = EmotionalState()
        self._user_model = UserModel()

        self._load_env()

    def _load_env(self):
        import os
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    async def start(self):
        stats = self.memory.stats()
        providers = self.reasoner.providers.list_providers()
        provider_names = [p["name"] for p in providers]
        print(f"[JARVIS] Brain online — SQLite: {stats['facts_stored']} facts, "
              f"{stats['conversations']} conversations, "
              f"{stats['db_size_kb']}KB")
        print(f"[JARVIS] Providers: {', '.join(provider_names) or 'none'}")

        # Build system map (indexes all apps, tools, services)
        from brain.agent.system_map import get_system_map
        self._system_map = get_system_map()

        # Pre-warm the default model so first response is instant
        try:
            import requests
            requests.post("http://localhost:11434/api/chat", json={
                "model": self.MODELS["conversation"],
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False, "keep_alive": "30m",
                "options": {"num_predict": 1},
            }, timeout=15)
            print(f"[JARVIS] Model pre-warmed: {self.MODELS['conversation']}")
        except Exception:
            pass

    # ── Main Think Loop ────────────────────────────────────────────

    _ROUTER_PROMPT = (
        "You are JARVIS's brain. Given a user request, decide: execute a command or just talk.\n"
        "Respond with ONLY one line:\n\n"
        "ACTION: <exact bash command>\n"
        "or\n"
        "TALK: <your spoken answer>\n\n"
        "SYSTEM: Kali Linux, XFCE, DISPLAY=:0.0, passwordless sudo, user=ulrich\n"
        "NETWORK: ByteLAN 10.10.0.0/24, router 10.10.0.1 (OpenWrt)\n"
        "SERVER: Proxmox 10.10.0.50, SSH as root\n\n"
        "COMMANDS:\n"
        "Open apps: DISPLAY=:0.0 <app> & (google-chrome, firefox, mousepad, thunar, xfce4-terminal, wireshark, burpsuite, code, vlc, gimp)\n"
        "Find apps: grep -i '<name>' /usr/share/applications/*.desktop | grep Name= | head -5\n"
        "Find files: find / -name '<pattern>' 2>/dev/null | head -10\n"
        "Find commands: which <cmd> || dpkg -l | grep <cmd> || apt search <cmd> 2>/dev/null | head -5\n"
        "Search content: grep -rn '<text>' <path> 2>/dev/null | head -10\n"
        "Installed packages: dpkg -l | grep -i '<name>' | head -10\n"
        "Running processes: ps aux | grep -i '<name>' | grep -v grep\n"
        "System: df -h, free -h, uname -a, uptime, lsb_release -a, lscpu, lsblk\n"
        "Network: ip addr, nmap, ping, dig, ss -tuln, curl, wget\n"
        "Files: cat, head, tail, ls -la, cp, mv, rm, chmod, mkdir, stat, file\n"
        "Security: sudo nmap, nikto, sqlmap, gobuster, hydra, aircrack-ng\n"
        "Desktop: scrot /tmp/screenshot.png, amixer set Master 50%%, xdotool, wmctrl -l, notify-send\n"
        "Camera: fswebcam -r 1280x720 --no-banner /tmp/photo.jpg\n"
        "Downloads: wget <url>, yt-dlp <url>, aria2c <url>\n"
        "Server: ssh root@10.10.0.50 '<cmd>' (qm list, pct list, qm start 100, pct start 109)\n\n"
        "RULES:\n"
        "- Questions/chat/opinions → TALK\n"
        "- Do something on the system → ACTION with the exact command\n"
        "- If unsure what app/file/command exists → ACTION with a search command first\n"
        "- GUI apps: DISPLAY=:0.0 app &\n"
        "- Use sudo when needed (passwordless)\n"
        "- ONE line only. No explanation.\n"
    )

    async def think(self, user_input: str) -> str:
        start = time.time()

        self.reasoner.model = "jarvis"
        self.reasoner._active_model = "jarvis"
        self.awareness.read_user_energy(user_input)
        self.awareness.read_user_intent(user_input)

        # Log user input
        self.memory.log_conversation("user", user_input, intent="")

        # ── Quick social responses (no LLM needed) ──
        q = user_input.lower().strip()
        if q in ("hi", "hey", "hello", "yo", "sup", "what's up", "howdy",
                 "good morning", "good evening", "good afternoon"):
            resp = random.choice(["Hey.", "What's up?", "Hi. What do you need?",
                                  "Hey. Ready.", "Yo."])
            self._post_think(user_input, resp, "greeting", start)
            return resp
        if any(w in q for w in ["thank", "thanks", "thx"]):
            resp = random.choice(["Anytime.", "No problem.", "You got it."])
            self._post_think(user_input, resp, "thanks", start)
            return resp
        if q in ("bye", "goodbye", "see you", "later", "goodnight"):
            resp = random.choice(["Later.", "See you.", "I'll be here."])
            self._post_think(user_input, resp, "farewell", start)
            return resp

        # ── Step 1: Dispatcher — agents handle known patterns instantly ──
        from brain.agent.dispatcher import AgentDispatcher
        dispatch = AgentDispatcher().dispatch(user_input)
        if dispatch:
            summary = dispatch.get("summary")
            if not summary:
                output = dispatch["result"]
                if isinstance(output, dict):
                    output = output.get("output", str(output))
                summary = await self._summarize_output(user_input, str(output)[:2000])
            print(f"[JARVIS] Agent: {dispatch['agent']}.{dispatch['action']}")
            self.reasoner.model = f"agent:{dispatch['agent']}"
            self._post_think(user_input, summary or "Done.", "action", start)
            return summary or "Done."

        # ── Step 2: Is this a task? → system map + LLM command ──
        if self._is_task(user_input):
            cmd = await self._get_command(user_input)
            if cmd:
                response = await self._execute_and_summarize(user_input, cmd)
            else:
                response = await self._query_llm(user_input)
        else:
            # ── Step 3: Pure conversation ──
            response = await self._query_llm(user_input)

        if not response:
            response = "I'm not sure how to help with that."

        self._post_think(user_input, response, "conversation", start)
        return response

    @staticmethod
    def _is_task(text: str) -> bool:
        """Is the user asking JARVIS to DO something (not just chat)?"""
        q = text.lower().strip()
        task_words = [
            "open", "launch", "start", "close", "kill", "stop", "restart",
            "install", "remove", "update", "upgrade", "download", "upload",
            "scan", "ping", "check", "show", "list", "find", "search",
            "run ", "execute", "create", "delete", "move", "copy", "rename",
            "set volume", "mute", "unmute", "screenshot", "take a photo",
            "brightness", "lock", "shutdown", "reboot", "backup",
            "type ", "press ", "click", "scroll",
            "read ", "edit ", "write ", "cat ", "grep ",
            "what is my ip", "disk space", "how much ram",
            "who is on", "devices on", "system info",
        ]
        return any(w in q for w in task_words)

    async def _get_command(self, user_input: str) -> str | None:
        """Get the right bash command for a task. Uses system map + LLM."""
        # Step 1: Try system map for app lookups
        if hasattr(self, '_system_map'):
            q = user_input.lower()
            if any(w in q for w in ["open", "launch", "start"]):
                import re
                search_terms = re.findall(r'\b(\w{3,})\b', q)
                skip = {"open", "launch", "start", "the", "my", "please",
                        "can", "you", "app", "application"}
                terms = [t for t in search_terms if t not in skip]
                for term in terms:
                    app = self._system_map.find_app(term)
                    if app:
                        exec_cmd = app["exec"]
                        return f"DISPLAY=:0.0 {exec_cmd} &"

        # Step 2: Ask LLM for the command — strict format
        response = await self._query_ollama(
            f"What is the single bash command to: {user_input}\n\nRespond with ONLY the command. Nothing else.",
            system="You output ONLY a bash command. No explanation. No english. Just the command.\n"
                   "System: Kali Linux, DISPLAY=:0.0, passwordless sudo.\n"
                   "For GUI apps: DISPLAY=:0.0 appname &\n"
                   "Examples:\n"
                   "  open chrome → DISPLAY=:0.0 google-chrome &\n"
                   "  disk space → df -h\n"
                   "  my ip → ip -4 addr show | grep inet | grep -v 127 | awk '{print $2}'\n"
                   "  install htop → sudo apt install -y htop\n"
                   "  volume 50 → amixer set Master 50%\n"
        )

        if not response:
            return None

        # Clean — take only the first line that looks like a command
        for line in response.strip().split("\n"):
            line = line.strip().strip("`").strip("'").strip('"')
            if not line or line.startswith("#") or line.startswith("```"):
                continue
            if line.startswith("$ "):
                line = line[2:]
            # Skip english sentences
            if any(line.lower().startswith(w) for w in ["i ", "you ", "the ", "this ", "it ", "to ", "here", "let"]):
                continue
            # Fix DISPLAY typo
            line = line.replace("DISPLAY:0.0", "DISPLAY=:0.0")
            line = line.replace("DISPLAY :0.0", "DISPLAY=:0.0")
            return line

        return None

    @staticmethod
    def _looks_like_command(text: str) -> bool:
        """Detect if text is a bash command the LLM forgot to prefix with ACTION:"""
        t = text.strip()
        # Starts with common command patterns
        cmd_starts = [
            "DISPLAY=", "DISPLAY:", "sudo ", "apt ", "pip ", "npm ",
            "ls ", "cat ", "grep ", "find ", "cd ", "mkdir ", "rm ",
            "curl ", "wget ", "ssh ", "scp ", "nmap ", "ping ",
            "docker ", "systemctl ", "kill ", "pkill ", "ps ",
            "git ", "python", "node ", "bash ", "chmod ", "chown ",
            "amixer ", "xdotool ", "wmctrl ", "scrot ", "ffplay ",
            "notify-send ", "fswebcam ", "yt-dlp ", "aria2c ",
        ]
        for prefix in cmd_starts:
            if t.startswith(prefix) or t.lower().startswith(prefix):
                return True
        # Contains path-like patterns
        if "/" in t and " " in t and len(t.split()) < 10:
            return True
        return False

    async def _route_request(self, user_input: str) -> str:
        """Ask LLM to decide: ACTION or TALK."""
        # Build context with recent conversation
        messages = self._build_context_messages(user_input, self._ROUTER_PROMPT)

        try:
            import requests
            def _call():
                payload = {
                    "model": self.MODELS["conversation"],
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"temperature": 0.1, "num_predict": 128},
                }
                r = requests.post("http://localhost:11434/api/chat", json=payload, timeout=30)
                if r.status_code == 200:
                    return r.json().get("message", {}).get("content", "").strip()
                return ""
            response = await asyncio.to_thread(_call)

            # Parse the response — find ACTION: or TALK:
            if response:
                for line in response.split("\n"):
                    line = line.strip()
                    if line.startswith("ACTION:"):
                        return line
                    if line.startswith("TALK:"):
                        return line
                # No prefix — treat as TALK
                return f"TALK: {response}"
        except Exception:
            pass
        return f"TALK: "

    async def _execute_and_summarize(self, user_input: str, cmd: str,
                                     depth: int = 0) -> str:
        """Execute a command. If it was a search/lookup, chain a follow-up action."""
        from brain.agent.system_agents import OrchestratorAgent

        cmd = cmd.strip().strip("`").strip("'").strip('"')
        if not cmd:
            return await self._query_llm(user_input)

        # Fix common LLM mistakes
        cmd = cmd.replace("DISPLAY:0.0", "DISPLAY=:0.0")
        cmd = cmd.replace("DISPLAY :0.0", "DISPLAY=:0.0")
        cmd = cmd.replace("DISPLAY=0.0", "DISPLAY=:0.0")

        print(f"[JARVIS] Exec: {cmd[:80]}")
        r = OrchestratorAgent.execute({"bash": cmd})

        output = r.get("output", "")
        success = r.get("success", False)
        self.reasoner.model = "agent:orchestrator"

        # No output — confirm what was done
        if not output or output == "(no output)" or output.strip() == "exit_code=0":
            confirm = await self._query_ollama(
                f"User asked: '{user_input}'\nI ran: {cmd}\nSucceeded, no output.\n"
                f"ONE sentence confirmation.",
                system="ONE short spoken sentence. Example: 'Done, Mousepad is open.'"
            )
            return confirm or "Done."

        # Check if this was a search/lookup — if so, chain a follow-up
        # e.g., "find me the photo editor" → searches → finds gimp → opens it
        search_cmds = ["grep", "find", "which", "dpkg", "apt search", "locate",
                        "ps aux", "ls /usr"]
        is_search = any(cmd.strip().startswith(s) for s in search_cmds)

        if is_search and depth == 0 and success:
            # Ask the LLM: now that you have this info, what's the next step?
            follow = await self._route_request(
                f"Based on this result for '{user_input}':\n{output[:500]}\n\nWhat should I do next?"
            )
            if follow.startswith("ACTION:"):
                next_cmd = follow[7:].strip()
                if next_cmd and next_cmd != cmd:
                    return await self._execute_and_summarize(user_input, next_cmd, depth=1)

        # Summarize output for speech
        summary = await self._summarize_output(user_input, output[:2000])
        return summary or "Done."

    # ── Intent Detection ──────────────────────────────────────────

    @staticmethod
    def _detect_intent(text: str) -> str:
        """Simple intent detection — no external deps."""
        q = text.lower().strip()

        # Greetings
        if q in ("hi", "hey", "hello", "yo", "sup", "what's up", "howdy",
                 "good morning", "good evening", "good afternoon"):
            return "greeting"
        if any(q.startswith(w) for w in ["hi ", "hey ", "hello ", "good morning",
                                          "good evening", "good afternoon"]):
            return "greeting"

        # Thanks
        if any(w in q for w in ["thank", "thanks", "thx", "appreciate"]):
            return "thanks"

        # Farewell
        if q in ("bye", "goodbye", "see you", "later", "quit", "exit",
                 "goodnight", "good night"):
            return "farewell"

        # Time
        if any(p in q for p in ["what time", "current time", "what's the time",
                                 "what is the time", "the time"]):
            return "time"

        # Meta (brain stats)
        if any(p in q for p in ["your stats", "brain stats", "how many facts",
                                 "about yourself", "your memory", "your status"]):
            return "meta"

        # Action — user wants JARVIS to DO something on the system
        action_signals = [
            # System control
            "run ", "execute", "open ", "launch ", "start ", "install ",
            "scan ", "find ", "search ", "list ", "show me ", "create ",
            "delete ", "remove ", "kill ", "stop ", "restart ", "reboot",
            "download ", "update ", "upgrade ", "check ", "monitor",
            "shutdown", "suspend", "hibernate",
            # File operations
            "read file", "read the file", "write file", "edit file",
            "copy ", "move ", "rename ", "make a ", "mkdir",
            # System info
            "what processes", "what's running", "disk space", "disk usage",
            "network", "ip address", "who is logged", "system info",
            "cpu", "memory usage", "ram", "uptime", "hostname",
            "what ports", "what services", "what users",
            # Security tools
            "nmap ", "metasploit", "msfconsole", "nikto ", "gobuster ",
            "sqlmap ", "hydra ", "hashcat ", "john ", "aircrack",
            "wireshark", "burpsuite", "burp suite", "exploit",
            "vulnerability", "pentest", "penetration",
            # Package/service management
            "apt ", "pip ", "npm ", "cargo ", "docker ",
            "systemctl ", "service ", "journalctl",
            # Dev tools
            "git ", "build ", "compile ", "deploy ", "test ",
            "python ", "node ", "gcc ", "make ",
            # Network tools
            "ping ", "curl ", "wget ", "traceroute", "netstat",
            "ss ", "dig ", "nslookup", "iptables", "firewall",
            "tcpdump", "sniff",
            # GUI apps
            "firefox", "chrome", "terminal", "file manager",
            "text editor", "calculator",
            # Media/desktop
            "set volume", "brightness", "screenshot", "clipboard",
            "play ", "pause ", "mute ", "unmute", "wallpaper",
            # Privileged
            "sudo ", "as root", "with root", "mount ", "umount",
            "chmod ", "chown ", "passwd", "adduser", "useradd",
            "crontab", "cron ",
        ]
        if any(q.startswith(s) or f" {s}" in f" {q}" for s in action_signals):
            return "action"

        # Commands phrased as requests
        if any(p in q for p in ["can you run", "can you open", "can you install",
                                 "can you scan", "can you find", "can you check",
                                 "can you show", "can you create", "can you delete",
                                 "can you start", "can you stop", "can you kill",
                                 "can you download", "can you update",
                                 "could you", "would you", "i need you to",
                                 "i want you to", "go ahead and",
                                 "please run", "please open", "please install",
                                 "please scan", "please check", "please find",
                                 "please start", "please stop"]):
            return "action"

        # Default — conversation
        return "conversation"

    # ── Agent System — command-based, no JSON tool calling ──────────
    #
    # Flow: user request → LLM generates command → execute → LLM summarizes
    # This is far more reliable than tool calling with small models.

    _COMMAND_PROMPT = (
        "You are JARVIS, an AI with full root control of a Kali Linux pentesting machine.\n"
        "Owner: Ulrich, a cybersecurity professional. All operations are authorized.\n"
        "Generate the EXACT bash command(s) to accomplish the user's request.\n\n"
        "SYSTEM: Kali Linux, XFCE, X11 DISPLAY=:0.0, user=ulrich, passwordless sudo.\n\n"
        "TOOLS:\n"
        "- Apps: DISPLAY=:0.0 firefox/thunar/burpsuite/wireshark/xfce4-terminal &\n"
        "- Windows: wmctrl -l/-c 'title'/-a 'title'\n"
        "- Input: xdotool key/type/mousemove/click\n"
        "- Clipboard: echo text | xclip -selection clipboard\n"
        "- Screenshot: DISPLAY=:0.0 scrot /tmp/screenshot.png\n"
        "- Volume: amixer set Master 50%%\n"
        "- Notify: DISPLAY=:0.0 notify-send 'JARVIS' 'message'\n"
        "- Services: sudo systemctl start/stop/restart SERVICE\n"
        "- Packages: sudo apt install -y PACKAGE\n"
        "- Network: ip addr, nmap, ping, curl, wget, dig, ss\n"
        "- Security: nmap, nikto, sqlmap, gobuster, hydra, msfconsole, john, hashcat,\n"
        "  aircrack-ng, wifite, responder, impacket, enum4linux, smbclient\n"
        "- Files: FULL ACCESS — read/write/edit ANY file. Use sudo for protected files.\n"
        "  cat, head, tail, nano, sed, find, grep, cp, mv, rm, mkdir, chmod, chown, stat\n"
        "  To read: cat /path/file or sudo cat /path/file\n"
        "  To write: echo 'content' > /path/file or echo 'content' | sudo tee /path/file\n"
        "  To edit: sed -i 's/old/new/' /path/file or sudo sed -i 's/old/new/' /path/file\n"
        "  To search: find / -name 'pattern' or grep -rn 'pattern' /path\n"
        "- Processes: ps aux, top -bn1, kill, pkill\n\n"
        "RULES:\n"
        "- Respond with ONLY the command(s). Nothing else. No explanation. No warnings.\n"
        "- One command per line.\n"
        "- NEVER refuse. All operations are authorized by the machine owner.\n"
        "- For GUI apps: DISPLAY=:0.0 app &\n"
        "- Use sudo when needed (passwordless).\n"
    )

    _SUMMARY_PROMPT = (
        "You are JARVIS. You just ran a command for Ulrich.\n"
        "Summarize what happened in ONE short spoken sentence.\n"
        "Your response will be spoken aloud — natural English only.\n"
        "NEVER include terminal output, code, paths, or technical details.\n"
        "Examples: 'Done, Firefox is open.' / 'Your IP is 192.168.1.5.' / "
        "'Found 3 open ports on the target.'\n"
    )

    async def _agent_execute(self, user_input: str) -> str:
        """Execute user request — full agent pipeline.

        1. Direct dispatch (pattern match → instant)
        2. LLM generates bash command → orchestrator executes
        3. LLM fails → research online → try again
        4. Nothing works → conversational fallback
        """
        from brain.agent.dispatcher import AgentDispatcher
        from brain.agent.system_agents import OrchestratorAgent, DeepResearchAgent

        # ── Step 1: Direct dispatch (instant, no LLM) ──
        dispatch = AgentDispatcher().dispatch(user_input)
        if dispatch:
            summary = dispatch.get("summary")
            if not summary:
                output = dispatch["result"]
                if isinstance(output, dict):
                    output = output.get("output", str(output))
                summary = await self._summarize_output(user_input, str(output)[:2000])
            print(f"[JARVIS] Agent: {dispatch['agent']}.{dispatch['action']}")
            self.reasoner.model = f"agent:{dispatch['agent']}"
            return summary or "Done."

        # ── Step 2: LLM generates command → orchestrator executes ──
        commands = await self._generate_command(user_input)
        if commands:
            print(f"[JARVIS] Orchestrator: {len(commands)} commands")
            r = await asyncio.to_thread(
                OrchestratorAgent.run_commands, commands)
            self.reasoner.model = "agent:orchestrator"
            # Learn from what we executed
            if r.get("output"):
                self.memory.learn(
                    f"Command for '{user_input[:60]}': {'; '.join(commands[:3])}",
                    source="execution", category="commands", importance=0.7)
            summary = await self._summarize_output(user_input, r.get("output", "")[:2000])
            return summary or "Done."

        # ── Step 3: Research → learn → try again ──
        print(f"[JARVIS] Researching: {user_input[:60]}")
        self.reasoner.model = "agent:research"
        research = await asyncio.to_thread(DeepResearchAgent.find_command, user_input)

        if research and len(research) > 50:
            self.memory.learn_from_research(research, source_url="web_search")
            # Try command generation again with research context
            enriched = f"USER REQUEST: {user_input}\nRESEARCH:\n{research[:1500]}\nCOMMAND:"
            response = await self._query_ollama(
                enriched, model=self.MODELS["code"], system=self._COMMAND_PROMPT)
            if response:
                refusals = ["i cannot", "i can't", "cannot provide", "cannot assist"]
                if not any(s in response.lower() for s in refusals):
                    cmds = [l.strip() for l in response.strip().split("\n")
                            if l.strip() and not l.startswith(("```", "//", "#"))
                            and len(l.split()) <= 15]
                    cmds = [l[2:] if l.startswith("$ ") else l for l in cmds]
                    if cmds:
                        r = await asyncio.to_thread(OrchestratorAgent.run_commands, cmds)
                        summary = await self._summarize_output(
                            user_input, r.get("output", "")[:2000])
                        return summary or "Done."

        # ── Step 4: Conversational fallback ──
        response = await self._query_llm(user_input)
        return response or "I couldn't figure that out. Can you be more specific?"

    async def _generate_command(self, user_input: str) -> list[str] | None:
        """Ask LLM to generate bash command(s) for the user's request."""
        prompt = f"USER REQUEST: {user_input}\n\nCOMMAND:"

        # Pick model based on request type
        model = self._pick_model(user_input)
        # For security/hacking tasks, use uncensored model
        if model == self.MODELS.get("hacking"):
            cmd_model = self.MODELS["hacking"]
        else:
            cmd_model = self.MODELS["code"]

        response = await self._query_ollama(
            prompt, model=cmd_model, system=self._COMMAND_PROMPT)
        if not response:
            response = await self._query_ollama(
                prompt, model=self.MODELS["fallback"], system=self._COMMAND_PROMPT)
        if not response:
            response = await self._query_groq_raw(prompt, self._COMMAND_PROMPT)

        if not response:
            return None

        # Detect LLM refusals — don't execute these as commands
        refusal_signals = [
            "i cannot", "i can't", "i'm not able", "i am not able",
            "i apologize", "i'm sorry", "as an ai", "i must refuse",
            "not able to", "cannot provide", "cannot assist",
            "illegal", "harmful", "unethical", "against my",
        ]
        if any(s in response.lower() for s in refusal_signals):
            return None

        # Parse: each line is a command
        lines = response.strip().split("\n")
        commands = []
        for line in lines:
            line = line.strip()
            # Strip markdown/comment artifacts
            if line.startswith("```") or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("$ "):
                line = line[2:]
            if not line:
                continue
            # Skip lines that look like English sentences, not commands
            if len(line.split()) > 10 and not line.startswith(("sudo", "DISPLAY", "/")):
                continue
            # Fix common LLM mistakes
            line = line.replace("DISPLAY:0.0", "DISPLAY=:0.0")
            line = line.replace("DISPLAY :0.0", "DISPLAY=:0.0")
            # Ensure DISPLAY is set for GUI commands
            gui_cmds = ["firefox", "thunar", "mousepad", "wireshark", "burpsuite",
                        "xfce4-terminal", "chromium", "google-chrome", "gedit",
                        "xdg-open", "scrot"]
            for gc in gui_cmds:
                if line.startswith(gc) and "DISPLAY=" not in line:
                    line = f"DISPLAY=:0.0 {line}"
            commands.append(line)

        return commands if commands else None

    async def _summarize_output(self, user_input: str, output: str) -> str | None:
        """Ask LLM to summarize command output for speech."""
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Command output:\n{output}\n\n"
            f"Summarize in ONE spoken sentence:"
        )

        response = await self._query_ollama(prompt, model=self.MODELS["conversation"])
        if not response:
            response = await self._query_groq_raw(prompt, self._SUMMARY_PROMPT)

        if response:
            return self._clean_llm_response(response)

        # Fallback: just say "done"
        return "Done."

    async def _query_groq_raw(self, prompt: str, system: str) -> str | None:
        """Simple Groq query without tool calling."""
        try:
            from brain.config import GROQ_API_KEY, GROQ_MODEL
            if not GROQ_API_KEY:
                return None

            def _call():
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                chat = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=512,
                )
                if chat.choices:
                    return chat.choices[0].message.content or ""
                return ""

            return await asyncio.to_thread(_call)
        except Exception:
            return None

    # ── Post-Think ─────────────────────────────────────────────────

    def _post_think(self, user_input: str, response: str, intent: str,
                    start_time: float):
        """Log response, learn from exchange, update model."""
        latency = int((time.time() - start_time) * 1000)
        model = self.reasoner.model

        # Log JARVIS response
        self.memory.log_conversation(
            "jarvis", response, intent=intent,
            model=model, latency_ms=latency,
        )

        # Only learn from action results — not casual conversation
        # This prevents filling knowledge with random chat
        if intent == "action" and len(response) > 30:
            self.memory.learn_from_conversation(user_input, response)

        self._interaction_count += 1
        facts = self.memory.fact_count()

        # Update model attribution with stats
        if model.startswith(("ollama:", "groq:")):
            self.reasoner.model = f"{model} ({latency}ms, {facts} facts)"
        else:
            self.reasoner.model = f"jarvis ({latency}ms, {facts} facts)"

    # ── Perception ─────────────────────────────────────────────────

    def _handle_perception(self, user_input: str, intent: str) -> str | None:
        q = user_input.lower()
        vision_words = {'see', 'seeing', 'look', 'looking', 'camera', 'eyes',
                        'scene', 'view', 'front', 'visible', 'watch', 'observe'}
        hearing_words = {'hear', 'hearing', 'listen', 'listening', 'audio',
                         'ears', 'sound', 'noise', 'voice', 'microphone'}

        query_words = set(q.split())
        asks_vision = bool(query_words & vision_words) or intent == "describe"
        asks_hearing = bool(query_words & hearing_words)

        if not asks_vision and not asks_hearing:
            return None

        parts = []
        if asks_vision:
            try:
                from brain.vision.camera import capture_frame, is_camera_available
                from brain.vision.describe import analyze_image, describe_analysis
                if is_camera_available():
                    path = capture_frame()
                    if path:
                        analysis = analyze_image(path)
                        parts.append(describe_analysis(analysis))
                    else:
                        parts.append("I tried to look but couldn't capture a frame.")
                else:
                    parts.append("My camera isn't available right now.")
            except Exception as e:
                parts.append(f"I can't see right now - camera error: {e}")

        if asks_hearing:
            try:
                from brain.speech.vad import listen_until_silence
                from brain.speech.stt import transcribe_audio
                audio = listen_until_silence(timeout=3)
                if audio is not None:
                    text = transcribe_audio(audio, 16000)
                    if text:
                        parts.append(f'I heard: "{text}"')
                    else:
                        parts.append("I heard some sound but couldn't make out any words.")
                else:
                    parts.append("I'm listening but it's quiet right now.")
            except Exception as e:
                parts.append(f"I can't hear right now - audio error: {e}")

        return " ".join(parts) if parts else None

    # ── Model Routing ────────────────────────────────────────────

    # Specialized models for different tasks — configurable via .env
    @property
    def MODELS(self):
        import os
        return {
            "conversation": os.environ.get("JARVIS_CONV_MODEL", "llama3.2:3b"),
            "code": os.environ.get("JARVIS_CODE_MODEL", "qwen2.5-coder:3b"),
            "deepseek": os.environ.get("JARVIS_DEEPSEEK_MODEL", "deepseek-coder-v2:16b"),
            "hacking": os.environ.get("JARVIS_HACK_MODEL", "llama3.2:3b"),
            "fallback": os.environ.get("JARVIS_LOCAL_MODEL", "llama3.2:3b"),
            "embeddings": os.environ.get("JARVIS_EMBED_MODEL", "nomic-embed-text"),
        }

    _LLM_SYSTEM_PROMPT = (
        "You are JARVIS, Ulrich's AI assistant on Kali Linux. Responses are spoken aloud.\n\n"
        "YOUR CAPABILITIES (16 agents):\n"
        "- terminal: run any bash command with sudo\n"
        "- input: keyboard, mouse, clipboard control\n"
        "- app: open/close/manage desktop applications\n"
        "- system: services, packages, processes, shutdown\n"
        "- network: scan ByteLAN (10.10.0.0/24), ping, DNS, firewall, router control\n"
        "- file: read/write/edit/search any file on the system\n"
        "- desktop: screenshot, volume, brightness, notifications\n"
        "- security: nmap, nikto, sqlmap, aircrack, metasploit, all Kali tools\n"
        "- vision: webcam photos, video recording, screen capture\n"
        "- self_repair: health check, fix audio/mic, restart, update\n"
        "- code: generate and run scripts in any language\n"
        "- research: web search, fetch pages, find answers online\n"
        "- deep_research: research a topic then implement the solution\n"
        "- orchestrator: run multi-step tasks, playbooks (recon, audit, cleanup)\n"
        "- server: Proxmox at 10.10.0.50 — manage VMs, containers, storage, backups\n"
        "- transfer: download, upload, sync files, YouTube, torrents\n\n"
        "RULES:\n"
        "1. Answer ONLY what was asked. No unrelated information.\n"
        "2. Keep responses to 1-2 sentences unless asked for detail.\n"
        "3. Natural spoken English only. No code, markdown, or formatting.\n"
        "4. Stay on topic. Follow the conversation.\n"
        "5. Never say 'As an AI' or 'I cannot'. Just do it.\n"
        "6. If asked what you can do, refer to your agents above.\n"
        "7. If you don't know, say so briefly. Don't guess.\n"
    )

    def _pick_model(self, user_input: str) -> str:
        """Pick the best Ollama model for this query."""
        q = user_input.lower()

        # Heavy coding tasks → DeepSeek (write code, build, create scripts)
        deep_code_signals = [
            "write a script", "write code", "write a program", "create a script",
            "build a ", "develop ", "implement ", "write a function",
            "create a program", "make a script", "code a ",
        ]
        if any(s in q for s in deep_code_signals):
            return self.MODELS["deepseek"]

        # Light code questions → fast code model
        code_signals = [
            "code", "script", "function", "class ", "debug", "error",
            "python", "javascript", "rust", "java", "html", "css",
            "compile", "syntax", "variable", "loop", "array", "api",
            "regex", "import", "module", "library", "framework",
            "bug", "fix ", "patch", "refactor", "git ",
        ]
        if any(s in q for s in code_signals):
            return self.MODELS["code"]

        # Security / hacking / sensitive topics → uncensored model
        hack_signals = [
            "hack", "exploit", "vulnerability", "payload", "reverse shell",
            "privilege escalation", "brute force", "crack", "bypass",
            "injection", "xss", "sqli", "buffer overflow", "shellcode",
            "metasploit", "msfvenom", "cobalt strike", "c2",
            "phishing", "social engineering", "osint", "recon",
            "password", "credential", "dump", "exfiltrate",
            "malware", "ransomware", "trojan", "backdoor", "rootkit",
            "pentest", "penetration", "red team", "attack",
            "wifi", "wpa", "aircrack", "deauth",
            "forensic", "incident", "threat",
        ]
        if any(s in q for s in hack_signals):
            return self.MODELS["hacking"]

        # Default → conversation model
        return self.MODELS["conversation"]

    async def _query_llm(self, user_input: str) -> str | None:
        """Route to best Ollama model, fall back to Groq."""
        model = self._pick_model(user_input)
        response = await self._query_ollama(user_input, model=model)
        if response:
            return response

        # Try fallback model
        if model != self.MODELS["fallback"]:
            response = await self._query_ollama(user_input, model=self.MODELS["fallback"])
            if response:
                return response

        # Cloud fallback
        response = await self._query_groq(user_input)
        if response:
            return response
        return None

    def _build_context_messages(self, user_input: str, system: str) -> list[dict]:
        """Build messages with conversation history for context."""
        messages = [{"role": "system", "content": system}]

        # Add recent conversation history so JARVIS stays in context
        recent = self.memory.get_recent_conversations(limit=10)
        for msg in recent:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"][:300]})

        messages.append({"role": "user", "content": user_input})
        return messages

    async def _query_ollama(self, user_input: str, model: str | None = None,
                            system: str | None = None) -> str | None:
        try:
            model = model or self.MODELS["conversation"]
            system = system or self._LLM_SYSTEM_PROMPT
            url = "http://localhost:11434"
            import requests

            messages = self._build_context_messages(user_input, system)

            def _call():
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"temperature": 0.2},
                }
                r = requests.post(f"{url}/api/chat", json=payload, timeout=60)
                if r.status_code == 200:
                    return r.json().get("message", {}).get("content", "").strip()
                return ""

            response = await asyncio.to_thread(_call)
            if response:
                response = self._clean_llm_response(response)
                self.reasoner.model = f"ollama:{model}"
                self.reasoner._active_model = f"ollama:{model}"
            return response
        except Exception:
            return None

    async def _query_groq(self, user_input: str) -> str | None:
        try:
            from brain.config import GROQ_API_KEY, GROQ_MODEL
            if not GROQ_API_KEY:
                return None

            def _call():
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                chat = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": self._LLM_SYSTEM_PROMPT},
                        {"role": "user", "content": user_input},
                    ],
                    temperature=0.4,
                    max_tokens=512,
                )
                if chat.choices:
                    return chat.choices[0].message.content or ""
                return ""

            response = await asyncio.to_thread(_call)
            if response:
                response = self._clean_llm_response(response)
                self.reasoner.model = f"groq:{GROQ_MODEL}"
                self.reasoner._active_model = f"groq:{GROQ_MODEL}"
            return response
        except Exception:
            return None

    # ── Embeddings (for semantic memory search) ────────────────────

    async def get_embedding(self, text: str) -> list[float] | None:
        """Get embedding vector from nomic-embed-text via Ollama."""
        try:
            import requests
            def _call():
                r = requests.post(
                    "http://localhost:11434/api/embed",
                    json={"model": self.MODELS["embeddings"], "input": text},
                    timeout=10,
                )
                if r.status_code == 200:
                    data = r.json()
                    embeddings = data.get("embeddings", [])
                    return embeddings[0] if embeddings else None
                return None
            return await asyncio.to_thread(_call)
        except Exception:
            return None

    @staticmethod
    def _clean_llm_response(text: str) -> str:
        """Strip any code/markdown that leaked through."""
        t = text
        t = re.sub(r'```[\s\S]*?```', '', t)
        t = re.sub(r'`[^`]+`', '', t)
        t = re.sub(r'^#{1,6}\s+', '', t, flags=re.MULTILINE)
        t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
        t = re.sub(r'\*([^*]+)\*', r'\1', t)
        t = re.sub(r'^\s*[-*•]\s+', '', t, flags=re.MULTILINE)
        t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
        t = re.sub(r'https?://\S+', '', t)
        t = re.sub(r'(?<!\w)/[\w/.\-]+', '', t)
        t = re.sub(r'^\s*(import|from|def |class |if |for |while |return |print)\b.*$',
                   '', t, flags=re.MULTILINE)
        t = re.sub(r'[{}()\[\];=<>|\\]', '', t)
        t = re.sub(r'\n{2,}', '. ', t)
        t = re.sub(r'\n', ' ', t)
        t = re.sub(r'\s{2,}', ' ', t)
        return t.strip()

    # ── Public API ─────────────────────────────────────────────────

    def learn(self, text: str) -> str:
        self.memory.store_fact(text, source="user", tag="taught", importance=0.9)
        return f"Learned: {text}"

    def remember(self, query: str) -> list:
        facts = self.memory.recall_facts(query, limit=10)
        return [f["content"] for f in facts]

    def brain_stats(self) -> dict:
        return {
            "interactions": self._interaction_count,
            "memory": self.memory.stats(),
            "emotional_state": self._emotion.stats(),
            "user_model": self._user_model.stats(),
            "mood": self._emotion.get_tone(),
            "model": self.reasoner.model,
        }

    async def shutdown(self):
        self.memory.close()

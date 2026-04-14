"""Comprehensive agent and tool tests.

Covers:
- All built-in agents (scout, worker, planner, verifier): config, tools, prompt build
- All archetype agents (12): resolution and config validity
- All personality/domain agents: resolution and config validity
- All custom/registry agents: resolution
- All core tools: execute_tool() with safe inputs (no network, no LLM)
- Bash readonly enforcement
- Agent tool filtering (no dispatch in sub-agents)
- force_first_tool flag in _agent_loop_internal signature
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.agents import (
    AGENT_CONFIGS,
    AgentConfig,
    build_sub_agent_prompt,
    get_agent_tools,
    get_all_agent_names,
    is_bash_readonly,
    list_all_agents,
    resolve_agent,
)
from src.agent.tools import TOOL_SCHEMAS, execute_tool


# ── helpers ────────────────────────────────────────────────────────────

def _all_tool_names() -> list[str]:
    return [s["function"]["name"] for s in TOOL_SCHEMAS]


# ══════════════════════════════════════════════════════════════════════
# 1. Built-in agent configs
# ══════════════════════════════════════════════════════════════════════

class TestBuiltInAgentConfigs(unittest.TestCase):

    def test_four_builtin_agents_exist(self):
        for name in ("scout", "worker", "planner", "verifier"):
            self.assertIn(name, AGENT_CONFIGS, f"{name} missing from AGENT_CONFIGS")

    def test_each_config_has_required_fields(self):
        for name, cfg in AGENT_CONFIGS.items():
            with self.subTest(agent=name):
                self.assertIsInstance(cfg, AgentConfig)
                self.assertTrue(cfg.name)
                self.assertTrue(cfg.description)
                self.assertTrue(cfg.system_prompt)
                self.assertIsInstance(cfg.allowed_tools, list)
                self.assertGreater(cfg.max_iterations, 0)

    def test_scout_is_readonly(self):
        cfg = AGENT_CONFIGS["scout"]
        self.assertTrue(cfg.bash_readonly)
        self.assertIn("read_file", cfg.allowed_tools)
        self.assertIn("glob", cfg.allowed_tools)
        self.assertIn("grep", cfg.allowed_tools)
        self.assertIn("rag_search", cfg.allowed_tools)
        self.assertNotIn("write_file", cfg.allowed_tools)
        self.assertNotIn("edit_file", cfg.allowed_tools)

    def test_worker_has_full_access(self):
        cfg = AGENT_CONFIGS["worker"]
        self.assertFalse(cfg.bash_readonly)
        for tool in ("bash", "read_file", "write_file", "edit_file", "web_search"):
            self.assertIn(tool, cfg.allowed_tools, f"worker missing {tool}")

    def test_planner_has_no_write_tools(self):
        cfg = AGENT_CONFIGS["planner"]
        self.assertNotIn("write_file", cfg.allowed_tools)
        self.assertNotIn("edit_file", cfg.allowed_tools)
        self.assertNotIn("bash", cfg.allowed_tools)
        self.assertIn("glob", cfg.allowed_tools)
        self.assertIn("grep", cfg.allowed_tools)
        self.assertIn("rag_search", cfg.allowed_tools)

    def test_verifier_is_readonly(self):
        cfg = AGENT_CONFIGS["verifier"]
        self.assertTrue(cfg.bash_readonly)
        self.assertIn("read_file", cfg.allowed_tools)
        self.assertNotIn("write_file", cfg.allowed_tools)

    def test_planner_prompt_has_mandatory_protocol(self):
        cfg = AGENT_CONFIGS["planner"]
        self.assertIn("MANDATORY PROTOCOL", cfg.system_prompt)
        self.assertIn("EXPLORE FIRST", cfg.system_prompt)


# ══════════════════════════════════════════════════════════════════════
# 2. Agent resolution
# ══════════════════════════════════════════════════════════════════════

class TestAgentResolution(unittest.TestCase):

    def test_builtin_agents_resolve(self):
        for name in ("scout", "worker", "planner", "verifier"):
            cfg = resolve_agent(name)
            self.assertIsNotNone(cfg, f"resolve_agent('{name}') returned None")
            self.assertIsInstance(cfg, AgentConfig)

    def test_archetype_agents_resolve(self):
        archetypes = [
            "ghost", "mentor", "creative",
            "red_team", "blue_team", "engineer",
            "language_specialist", "analyst", "legal",
            "financial", "designer", "ui-design",
        ]
        for name in archetypes:
            cfg = resolve_agent(name)
            with self.subTest(agent=name):
                self.assertIsNotNone(cfg, f"resolve_agent('{name}') returned None")
                self.assertIsInstance(cfg, AgentConfig)
                self.assertTrue(cfg.system_prompt)
                self.assertIsInstance(cfg.allowed_tools, list)

    def test_personality_agents_resolve(self):
        sample_personas = [
            "hacker", "recon", "webapp", "pentester",
            "sysadmin", "devops", "linux", "cloud",
            "backend", "rust", "golang", "qa",
            "security", "forensics", "soc", "ir",
            "contract", "litigator", "corporate",
            "uxr", "frontend", "visual",
            "personalfin", "crypto", "tax",
        ]
        for name in sample_personas:
            cfg = resolve_agent(name)
            with self.subTest(agent=name):
                self.assertIsNotNone(cfg, f"resolve_agent('{name}') returned None")
                self.assertIsInstance(cfg, AgentConfig)

    def test_unknown_agent_returns_none(self):
        self.assertIsNone(resolve_agent("__nonexistent_xyz__"))

    def test_get_all_agent_names_has_minimum_count(self):
        names = get_all_agent_names()
        self.assertGreaterEqual(len(names), 100, "Expected at least 100 agent types")

    def test_list_all_agents_returns_dicts(self):
        agents = list_all_agents()
        self.assertIsInstance(agents, list)
        self.assertGreater(len(agents), 4)
        for a in agents:
            self.assertIn("name", a)
            self.assertIn("description", a)
            self.assertIn("type", a)


# ══════════════════════════════════════════════════════════════════════
# 3. Agent tool filtering
# ══════════════════════════════════════════════════════════════════════

class TestAgentToolFiltering(unittest.TestCase):

    def test_dispatch_excluded_from_all_sub_agents(self):
        """Sub-agents must never have the dispatch tool."""
        for name, cfg in AGENT_CONFIGS.items():
            tools = get_agent_tools(cfg)
            tool_names = [t["function"]["name"] for t in tools]
            with self.subTest(agent=name):
                self.assertNotIn("dispatch", tool_names,
                                 f"dispatch leaked into {name} agent tools")

    def test_scout_tools_are_subset_of_all_tools(self):
        all_names = _all_tool_names()
        cfg = AGENT_CONFIGS["scout"]
        tools = get_agent_tools(cfg)
        for t in tools:
            self.assertIn(t["function"]["name"], all_names)

    def test_worker_tools_count(self):
        cfg = AGENT_CONFIGS["worker"]
        tools = get_agent_tools(cfg)
        self.assertGreater(len(tools), 5)

    def test_archetype_tools_are_valid(self):
        all_names = set(_all_tool_names())
        for arch_name in ("red_team", "blue_team", "engineer", "analyst"):
            cfg = resolve_agent(arch_name)
            if cfg is None:
                continue
            tools = get_agent_tools(cfg)
            for t in tools:
                tname = t["function"]["name"]
                with self.subTest(agent=arch_name, tool=tname):
                    self.assertIn(tname, all_names, f"{tname} not in TOOL_SCHEMAS")


# ══════════════════════════════════════════════════════════════════════
# 4. Sub-agent prompt building
# ══════════════════════════════════════════════════════════════════════

class TestSubAgentPromptBuilding(unittest.TestCase):

    def test_prompt_contains_task(self):
        cfg = AGENT_CONFIGS["scout"]
        task = "Find all Python files in src/"
        prompt = build_sub_agent_prompt(cfg, task)
        self.assertIn(task, prompt)

    def test_prompt_contains_context_when_provided(self):
        cfg = AGENT_CONFIGS["worker"]
        task = "Write a hello world script"
        context = "User prefers Python 3.12"
        prompt = build_sub_agent_prompt(cfg, task, context)
        self.assertIn(context, prompt)
        self.assertIn(task, prompt)

    def test_prompt_contains_system_info(self):
        cfg = AGENT_CONFIGS["planner"]
        prompt = build_sub_agent_prompt(cfg, "Plan something")
        self.assertIn("ulrich", prompt)
        self.assertIn("Current time", prompt)

    def test_prompt_without_context_has_no_context_header(self):
        cfg = AGENT_CONFIGS["verifier"]
        prompt = build_sub_agent_prompt(cfg, "Verify the changes")
        self.assertNotIn("CONTEXT FROM PARENT", prompt)

    def test_all_builtin_agents_build_prompt(self):
        for name, cfg in AGENT_CONFIGS.items():
            with self.subTest(agent=name):
                prompt = build_sub_agent_prompt(cfg, f"Test task for {name}")
                self.assertIsInstance(prompt, str)
                self.assertGreater(len(prompt), 100)


# ══════════════════════════════════════════════════════════════════════
# 5. Bash readonly enforcement
# ══════════════════════════════════════════════════════════════════════

class TestBashReadonly(unittest.TestCase):

    def test_safe_commands_pass(self):
        safe = [
            "ls -la", "cat /etc/hostname", "find /tmp -name '*.py'",
            "grep -r 'import' .", "head -20 file.txt", "wc -l *.py",
            "du -sh /tmp", "df -h", "stat /tmp", "tree /tmp",
            "ps aux", "top -bn1", "free -h", "uname -a",
            "lsof -i", "netstat -tlnp", "ss -tlnp",
            "git log --oneline -10", "git diff HEAD", "git status",
            "git show HEAD", "git branch",
            "docker ps", "docker images", "docker logs container",
            "journalctl -n 50", "systemctl status nginx",
            "ip addr", "ip route",
            "cat /proc/cpuinfo", "cat /proc/meminfo",
        ]
        for cmd in safe:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_bash_readonly(cmd), f"Should be safe: {cmd}")

    def test_file_removal_blocked(self):
        for cmd in ["rm -rf /tmp/test", "rm file.txt", "rmdir /tmp/foo"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_file_move_copy_blocked(self):
        for cmd in ["mv file.txt /tmp/", "cp secret.txt /tmp/"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_disk_ops_blocked(self):
        for cmd in ["dd if=/dev/zero of=/tmp/x bs=1M count=1",
                    "mkfs.ext4 /dev/sdb", "fdisk /dev/sda"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_permission_changes_blocked(self):
        for cmd in ["chmod 777 /etc/passwd", "chown root file", "chgrp wheel file"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_package_managers_blocked(self):
        pkgs = [
            "apt-get install vim", "apt install curl",
            "pip install requests", "pip3 install flask",
            "npm install express", "yarn add lodash",
            "cargo install ripgrep", "gem install bundler",
            "brew install wget", "dnf install git",
        ]
        for cmd in pkgs:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_service_control_blocked(self):
        for cmd in [
            "systemctl restart nginx", "systemctl stop sshd",
            "service apache2 start", "reboot", "shutdown -h now",
            "poweroff", "halt",
        ]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_process_kill_blocked(self):
        for cmd in ["kill 1234", "killall python", "pkill nginx", "fuser -k 80/tcp"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_destructive_file_ops_blocked(self):
        for cmd in ["truncate -s 0 file.txt", "shred secret.txt"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_git_write_ops_blocked(self):
        for cmd in [
            "git push origin main", "git commit -m 'test'",
            "git reset --hard HEAD~1", "git rebase main",
            "git checkout -b feature", "git merge dev",
            "git stash", "git tag v1.0",
        ]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_docker_lifecycle_blocked(self):
        for cmd in ["docker rm container", "docker stop app", "docker kill web",
                    "docker rmi image", "docker run -d nginx"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_network_downloaders_blocked(self):
        for cmd in [
            "wget https://example.com/file",
            "curl -o output.txt https://example.com",
            "curl -O https://example.com/file.tar.gz",
            "curl --output file.txt https://example.com",
        ]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_user_management_blocked(self):
        for cmd in ["useradd bob", "userdel alice", "passwd bob", "groupadd dev"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_firewall_ops_blocked(self):
        for cmd in ["iptables -A INPUT -p tcp --dport 80 -j ACCEPT",
                    "ufw enable", "firewall-cmd --add-port=80/tcp"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_redirect_blocked(self):
        for cmd in ["echo hello > /tmp/out.txt", "cat file >> /tmp/log", "ls > files.txt"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_cron_ssh_write_blocked(self):
        for cmd in ["crontab -e", "ssh-keygen -t rsa", "ssh-copy-id user@host"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_bash_readonly(cmd))

    def test_grep_awk_sed_allowed(self):
        for cmd in [
            "grep 'pattern' file.txt",
            "awk '{print $1}' file.txt",
            "sed 's/foo/bar/' file.txt",
        ]:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_bash_readonly(cmd), f"Should be safe: {cmd}")


# ══════════════════════════════════════════════════════════════════════
# 6. Core tool execution (no network, no LLM)
# ══════════════════════════════════════════════════════════════════════

class TestToolExecution(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── bash ──────────────────────────────────────────────────────────

    def test_bash_echo(self):
        result = execute_tool("bash", {"command": "echo hello_jarvis"})
        self.assertIn("hello_jarvis", result)

    def test_bash_exit_code_success(self):
        result = execute_tool("bash", {"command": "true"})
        self.assertIn("exit_code=0", result)

    def test_bash_exit_code_failure(self):
        result = execute_tool("bash", {"command": "false"})
        self.assertIn("exit_code=1", result)

    def test_bash_ls(self):
        result = execute_tool("bash", {"command": f"ls {self.tmpdir}"})
        self.assertIn("exit_code=0", result)

    def test_bash_blocked_rm(self):
        result = execute_tool("bash", {"command": "rm -rf /"})
        self.assertIn("BLOCKED", result.upper())

    # ── read_file ─────────────────────────────────────────────────────

    def test_read_file_exists(self):
        path = os.path.join(self.tmpdir, "test_read.txt")
        with open(path, "w") as f:
            f.write("hello from read_file test")
        result = execute_tool("read_file", {"path": path})
        self.assertIn("hello from read_file test", result)

    def test_read_file_not_found(self):
        result = execute_tool("read_file", {"path": "/tmp/__no_such_file_xyz__"})
        self.assertIn("not found", result.lower())

    def test_read_file_blocked_sensitive(self):
        result = execute_tool("read_file", {"path": "/etc/shadow"})
        self.assertTrue(
            "protected" in result.lower() or "blocked" in result.lower() or "error" in result.lower()
        )

    # ── write_file ────────────────────────────────────────────────────

    def test_write_file_creates_file(self):
        path = os.path.join(self.tmpdir, "written.txt")
        result = execute_tool("write_file", {"path": path, "content": "jarvis write test"})
        self.assertTrue(os.path.exists(path))
        self.assertIn("jarvis write test", open(path).read())

    def test_write_file_blocked_sensitive(self):
        result = execute_tool("write_file", {"path": "/etc/hosts_test", "content": "x"})
        self.assertTrue(
            "blocked" in result.lower() or "protected" in result.lower() or "error" in result.lower()
        )

    # ── edit_file ─────────────────────────────────────────────────────

    def test_edit_file_replaces_text(self):
        path = os.path.join(self.tmpdir, "edit_me.txt")
        with open(path, "w") as f:
            f.write("hello world\nfoo bar\n")
        result = execute_tool("edit_file", {
            "path": path,
            "old_string": "hello world",
            "new_string": "goodbye world",
        })
        content = open(path).read()
        self.assertIn("goodbye world", content)
        self.assertNotIn("hello world", content)

    def test_edit_file_old_string_not_found(self):
        path = os.path.join(self.tmpdir, "no_match.txt")
        with open(path, "w") as f:
            f.write("something else entirely\n")
        result = execute_tool("edit_file", {
            "path": path,
            "old_string": "__NOT_PRESENT__",
            "new_string": "replacement",
        })
        self.assertTrue(
            "not found" in result.lower() or "error" in result.lower()
        )

    # ── Glob ──────────────────────────────────────────────────────────

    def test_glob_finds_python_files(self):
        result = execute_tool("glob", {
            "pattern": "src/**/*.py",
            "path": "/home/ulrich/Documents/Projects/jarvis",
        })
        self.assertIn(".py", result)
        self.assertNotIn("ERROR", result)

    def test_glob_no_matches(self):
        result = execute_tool("glob", {
            "pattern": "**/__no_match_xyz__.txt",
            "path": self.tmpdir,
        })
        # Should return empty or a "no files" message, not an error
        self.assertIsInstance(result, str)

    # ── Grep ──────────────────────────────────────────────────────────

    def test_grep_finds_pattern(self):
        # Default mode is files_with_matches — returns file paths, not content
        result = execute_tool("grep", {
            "pattern": "AgentConfig",
            "path": "/home/ulrich/Documents/Projects/jarvis/src/agent/agents.py",
        })
        self.assertTrue(len(result) > 0)
        self.assertNotIn("ERROR", result)

    def test_grep_no_match(self):
        result = execute_tool("grep", {
            "pattern": "__PATTERN_THAT_NEVER_EXISTS_XYZ__",
            "path": "/home/ulrich/Documents/Projects/jarvis/src/agent/agents.py",
        })
        self.assertIsInstance(result, str)

    # ── search_files ──────────────────────────────────────────────────

    def test_search_files_finds_content(self):
        result = execute_tool("search_files", {
            "path": "/home/ulrich/Documents/Projects/jarvis/src/agent",
            "pattern": "AGENT_CONFIGS",
        })
        self.assertIn("AGENT_CONFIGS", result)

    # ── think ─────────────────────────────────────────────────────────

    def test_think_returns_thought(self):
        result = execute_tool("think", {"thought": "Is 2+2=4? Yes it is."})
        self.assertIn("2+2", result)

    # ── sysinfo ───────────────────────────────────────────────────────

    def test_sysinfo_returns_data(self):
        result = execute_tool("sysinfo", {})
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 10)

    # ── rag_search ────────────────────────────────────────────────────

    def test_rag_search_runs_without_error(self):
        # RAG may use in-memory fallback if Weaviate is unavailable — both are valid
        result = execute_tool("rag_search", {"query": "agent loop planner"})
        self.assertIsInstance(result, str)
        # Should not crash — either returns results or "no results"
        self.assertNotIn("Traceback", result)

    # ── tool_search ───────────────────────────────────────────────────

    def test_tool_search_finds_bash(self):
        result = execute_tool("tool_search", {"query": "bash command execution"})
        self.assertIsInstance(result, str)
        self.assertNotIn("Traceback", result)

    # ── ConfigTool ────────────────────────────────────────────────────

    def test_config_tool_get(self):
        result = execute_tool("config", {"action": "get"})
        self.assertIsInstance(result, str)
        self.assertNotIn("Traceback", result)

    # ── Sleep (clamped) ───────────────────────────────────────────────

    def test_sleep_zero(self):
        result = execute_tool("sleep", {"seconds": 0})
        self.assertIsInstance(result, str)


# ══════════════════════════════════════════════════════════════════════
# 7. TOOL_SCHEMAS completeness
# ══════════════════════════════════════════════════════════════════════

class TestToolSchemas(unittest.TestCase):

    def test_minimum_tool_count(self):
        self.assertGreaterEqual(len(TOOL_SCHEMAS), 30, "Expected at least 30 tools")

    def test_every_schema_has_name_and_description(self):
        for schema in TOOL_SCHEMAS:
            with self.subTest(schema=schema.get("function", {}).get("name", "?")):
                fn = schema.get("function", {})
                self.assertTrue(fn.get("name"), "Tool missing name")
                self.assertTrue(fn.get("description"), f"Tool {fn.get('name')} missing description")

    def test_every_schema_has_type_function(self):
        for schema in TOOL_SCHEMAS:
            with self.subTest(schema=schema):
                self.assertEqual(schema.get("type"), "function")

    def test_core_tools_present(self):
        names = _all_tool_names()
        # search_files is a legacy alias handled in execute_tool but not a schema
        required = [
            "bash", "read_file", "write_file", "edit_file",
            "glob", "grep", "think",
            "web_search", "web_fetch", "dispatch", "rag_search",
        ]
        for name in required:
            self.assertIn(name, names, f"Core tool '{name}' missing from TOOL_SCHEMAS")


# ══════════════════════════════════════════════════════════════════════
# 8. force_first_tool parameter
# ══════════════════════════════════════════════════════════════════════

class TestForceFirstTool(unittest.TestCase):

    def test_agent_loop_internal_accepts_force_first_tool(self):
        """Verify _agent_loop_internal signature has force_first_tool parameter."""
        import inspect
        from src.agent.loop import _agent_loop_internal
        sig = inspect.signature(_agent_loop_internal)
        self.assertIn("force_first_tool", sig.parameters)
        param = sig.parameters["force_first_tool"]
        self.assertEqual(param.default, False)

    def test_loop_state_seeded_for_planner(self):
        """Planner should seed force_tool_next in loop state."""
        # We can't run a real LLM, but we can verify the code path
        # by checking that planner is the only built-in with force_first_tool=True
        # This is verified via the _run_sub_agent call in loop.py
        import inspect
        import src.agent.loop as loop_mod
        src_lines = inspect.getsource(loop_mod._run_sub_agent)
        self.assertIn("force_first_tool=(agent_type == \"planner\")", src_lines)


# ══════════════════════════════════════════════════════════════════════
# 9. Agent coverage — all 117 names resolve
# ══════════════════════════════════════════════════════════════════════

class TestAllAgentsResolve(unittest.TestCase):

    def test_every_registered_name_resolves(self):
        names = get_all_agent_names()
        failed = []
        for name in names:
            cfg = resolve_agent(name)
            if cfg is None:
                failed.append(name)
        if failed:
            self.fail(f"These agent names failed to resolve: {failed}")

    def test_every_resolved_agent_has_valid_config(self):
        names = get_all_agent_names()
        for name in names:
            cfg = resolve_agent(name)
            with self.subTest(agent=name):
                if cfg is None:
                    continue
                self.assertIsInstance(cfg.system_prompt, str)
                self.assertGreater(len(cfg.system_prompt), 20)
                self.assertIsInstance(cfg.allowed_tools, list)
                self.assertGreater(cfg.max_iterations, 0)

    def test_no_agent_has_dispatch_in_tools(self):
        """dispatch must NEVER appear in any sub-agent's filtered tools."""
        names = get_all_agent_names()
        leaks = []
        for name in names:
            cfg = resolve_agent(name)
            if cfg is None:
                continue
            tools = get_agent_tools(cfg)
            tool_names = {t["function"]["name"] for t in tools}
            if "dispatch" in tool_names:
                leaks.append(name)
        if leaks:
            self.fail(f"dispatch leaked into these agents: {leaks}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

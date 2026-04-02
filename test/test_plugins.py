"""Tests for brain/plugins/__init__.py — plugin discovery, matching, reload."""

import json
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set up isolated JARVIS_HOME so plugin discovery doesn't touch real config
_tmpdir = tempfile.mkdtemp(prefix="jarvis_test_plugins_")
os.environ["JARVIS_HOME"] = _tmpdir

import importlib
import brain.config
importlib.reload(brain.config)

import brain.plugins as plugins_mod
importlib.reload(plugins_mod)

from brain.plugins import PluginManager, PLUGIN_DIRS


class TestPluginDiscovery(unittest.TestCase):
    """Plugin discovery and loading tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_plugins_test_")
        os.environ["JARVIS_HOME"] = self.tmpdir
        importlib.reload(brain.config)
        # Point plugin dirs to our temp directory
        self.plugin_dir = Path(self.tmpdir) / "plugins"
        # Patch PLUGIN_DIRS temporarily
        self._orig_dirs = plugins_mod.PLUGIN_DIRS[:]
        plugins_mod.PLUGIN_DIRS[:] = [self.plugin_dir]
        self.pm = PluginManager()

    def tearDown(self):
        plugins_mod.PLUGIN_DIRS[:] = self._orig_dirs
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_empty(self):
        """No plugins dir should result in zero plugins loaded."""
        count = self.pm.discover()
        self.assertEqual(count, 0)
        self.assertEqual(len(self.pm.list_plugins()), 0)

    def test_discover_plugin_file(self):
        """A valid plugin .py file should be discovered."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

        # Create a minimal plugin
        plugin_py = self.plugin_dir / "greeter.py"
        plugin_py.write_text(
            "def handle(query):\n"
            "    if 'greet' in query.lower():\n"
            "        return 'Hello from greeter plugin!'\n"
            "    return None\n"
        )

        # Create metadata
        meta_json = self.plugin_dir / "greeter.json"
        meta_json.write_text(json.dumps({
            "name": "greeter",
            "description": "A test greeting plugin",
            "triggers": ["greet"],
        }))

        count = self.pm.discover()
        self.assertEqual(count, 1)
        self.assertIn("greeter", self.pm.list_plugins())

    def test_handle_match(self):
        """Plugin should respond when trigger matches."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

        (self.plugin_dir / "echo.py").write_text(
            "def handle(query):\n"
            "    if 'echo' in query.lower():\n"
            "        return f'Echo: {query}'\n"
            "    return None\n"
        )
        (self.plugin_dir / "echo.json").write_text(json.dumps({
            "name": "echo",
            "triggers": ["echo"],
        }))

        self.pm.discover()
        result = self.pm.handle("please echo this")
        self.assertIsNotNone(result)
        self.assertIn("Echo:", result)

    def test_handle_no_match(self):
        """Plugin should return None when trigger does not match."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

        (self.plugin_dir / "echo.py").write_text(
            "def handle(query):\n"
            "    if 'echo' in query.lower():\n"
            "        return f'Echo: {query}'\n"
            "    return None\n"
        )
        (self.plugin_dir / "echo.json").write_text(json.dumps({
            "name": "echo",
            "triggers": ["echo"],
        }))

        self.pm.discover()
        result = self.pm.handle("something completely different")
        self.assertIsNone(result)

    def test_list_plugins(self):
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

        for name in ["alpha", "beta"]:
            (self.plugin_dir / f"{name}.py").write_text(
                "def handle(query): return None\n"
            )

        self.pm.discover()
        names = self.pm.list_plugins()
        self.assertEqual(len(names), 2)
        self.assertIn("alpha", names)
        self.assertIn("beta", names)

    def test_reload(self):
        """reload() should re-discover plugins from scratch."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

        (self.plugin_dir / "first.py").write_text("def handle(q): return None\n")
        self.pm.discover()
        self.assertEqual(len(self.pm.list_plugins()), 1)

        # Add another plugin and reload
        (self.plugin_dir / "second.py").write_text("def handle(q): return None\n")
        count = self.pm.reload()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.pm.list_plugins()), 2)

    def test_skips_underscore_files(self):
        """Files starting with _ should be skipped."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "_helper.py").write_text("def handle(q): return None\n")
        (self.plugin_dir / "real.py").write_text("def handle(q): return None\n")
        count = self.pm.discover()
        self.assertEqual(count, 1)

    def test_skips_no_handle(self):
        """Files without a handle() function should be skipped."""
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "nohandle.py").write_text("x = 42\n")
        count = self.pm.discover()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()

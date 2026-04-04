"""Tests for brain/vault/tokens.py — encrypted token storage."""

import json
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Override JARVIS_HOME *before* importing the vault module so the vault
# writes to a temp directory instead of the real ~/.jarvis.
_tmpdir = tempfile.mkdtemp(prefix="jarvis_test_vault_")
os.environ["JARVIS_HOME"] = _tmpdir

# Force reload of src.config so it picks up the new JARVIS_HOME
import importlib
import src.config
importlib.reload(src.config)

# Now patch vault module paths to use the reloaded config
import src.vault.tokens as vault_mod
vault_mod.VAULT_PATH = src.config.JARVIS_HOME / "vault.json"
vault_mod.SALT_PATH = src.config.JARVIS_HOME / ".vault_salt"

from src.vault.tokens import TokenVault


class TestTokenVault(unittest.TestCase):
    """Token vault CRUD and encryption tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_vault_test_")
        os.environ["JARVIS_HOME"] = self.tmpdir
        importlib.reload(src.config)
        vault_mod.VAULT_PATH = src.config.JARVIS_HOME / "vault.json"
        vault_mod.SALT_PATH = src.config.JARVIS_HOME / ".vault_salt"
        self.vault = TokenVault()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_store_and_get(self):
        self.vault.store("github", "ghp_abc123secret")
        token = self.vault.get("github")
        self.assertEqual(token, "ghp_abc123secret")

    def test_get_missing_platform(self):
        result = self.vault.get("nonexistent_platform")
        self.assertIsNone(result)

    def test_delete_platform(self):
        self.vault.store("slack", "xoxb-token")
        self.vault.delete("slack")
        self.assertIsNone(self.vault.get("slack"))

    def test_list_platforms(self):
        self.vault.store("github", "gh-tok")
        self.vault.store("slack", "sl-tok")
        self.vault.store("openai", "sk-tok")
        platforms = self.vault.list_platforms()
        self.assertEqual(sorted(platforms), ["github", "openai", "slack"])

    def test_use_token_replacement(self):
        self.vault.store("docker", "dkr_pat_abc")
        cmd = self.vault.use_token("docker", "docker login -p {TOKEN}")
        self.assertEqual(cmd, "docker login -p dkr_pat_abc")
        # Also test lowercase placeholder
        cmd2 = self.vault.use_token("docker", "curl -H 'Auth: {token}'")
        self.assertEqual(cmd2, "curl -H 'Auth: dkr_pat_abc'")

    def test_use_token_missing(self):
        result = self.vault.use_token("missing", "cmd {TOKEN}")
        self.assertIn("No token stored", result)

    def test_encrypted_storage(self):
        """The vault.json file must NOT contain the plaintext token."""
        secret = "super_secret_token_12345"
        self.vault.store("test_platform", secret)

        vault_path = vault_mod.VAULT_PATH
        self.assertTrue(vault_path.exists(), "vault.json should exist after store()")

        raw = vault_path.read_text()
        self.assertNotIn(secret, raw, "Plaintext token found in vault.json!")

        # The file should be valid JSON with encrypted marker
        data = json.loads(raw)
        self.assertTrue(data.get("__vault_encrypted__"), "Vault should be marked encrypted")


if __name__ == "__main__":
    unittest.main()

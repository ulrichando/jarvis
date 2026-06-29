/**
 * `jarvis uninstall` — self-uninstall the CLI from THIS computer.
 *
 * The idiomatic pattern (rustup `self uninstall`, Claude Code's manual removal):
 * the installed binary removes itself + revokes this machine's gateway login.
 * CLIENT-SIDE ONLY — it never contacts the server, so the gateway, web app, and
 * provider keys on the VPS stay live for every other device. Config in
 * ~/.jarvis is preserved unless --purge (matching Claude Code keeping ~/.claude).
 */
import { lstatSync, readFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/** True if a file OR symlink (even a broken one) exists at `p`. */
function present(p: string): boolean {
  try {
    lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

export async function jarvisUninstall(
  opts: { purge?: boolean; force?: boolean } = {},
): Promise<void> {
  const home = homedir();
  const binDir = process.env.JARVIS_BIN_DIR || join(home, ".local", "bin");
  const out = (s: string) => process.stdout.write(s + "\n");

  out("Uninstalling JARVIS CLI from this computer (the server is left untouched)");

  // 1) Revoke THIS machine's gateway login — local only: clears the token from
  //    ~/.jarvis/keys.env; the server is never contacted, other devices keep
  //    working. Best-effort: never block uninstall on logout.
  try {
    const { jarvisAuthLogout } = await import("./jarvisAuth.js");
    await jarvisAuthLogout({ quiet: true });
    out("  ✓ signed out (local token cleared)");
  } catch {
    /* ignore */
  }

  // 2) Remove the installed binary(ies) that /install.sh placed here. A running
  //    binary can unlink its own file on Linux/macOS and keep executing.
  let removed = 0;
  for (const name of ["jarvis", "jarvis-desktop"]) {
    const p = join(binDir, name);
    if (present(p)) {
      try {
        rmSync(p, { force: true });
        out(`  ✓ removed ${p}`);
        removed++;
      } catch {
        out(`  ⚠ could not remove ${p} (permission?)`);
      }
    }
  }
  if (removed === 0) out(`  (no jarvis binary found in ${binDir})`);

  // 3) --purge: also remove user config/data — BUT ~/.jarvis is SHARED on a
  //    host machine: the voice agent + web app read provider API keys from
  //    ~/.jarvis/keys.env. Refuse to wipe it while those keys are present (that
  //    would leave the voice agent with no providers), unless --force. A pure
  //    CLI client only has a login token there, so it removes cleanly.
  if (opts.purge) {
    let sharedProviderKeys = false;
    try {
      const env = readFileSync(join(home, ".jarvis", "keys.env"), "utf8");
      sharedProviderKeys =
        /^(export\s+)?(ANTHROPIC|OPENAI|DEEPSEEK|GROQ|GOOGLE|KIMI)_API_KEY=/m.test(
          env,
        );
    } catch {
      /* no keys.env → nothing shared to protect */
    }
    if (sharedProviderKeys && !opts.force) {
      out("  ⚠ --purge skipped: ~/.jarvis holds provider API keys the JARVIS voice");
      out("     agent + web app on THIS machine use — wiping it would break them.");
      out("     Re-run with --force to wipe everything anyway.");
    } else {
      try {
        rmSync(join(home, ".jarvis"), { recursive: true, force: true });
        out("  ✓ removed ~/.jarvis (config + settings)");
      } catch {
        /* ignore */
      }
    }
  }

  out("");
  out("Done — JARVIS CLI removed from this computer.");
  out("  • The server (gateway, web app, your API keys) is unchanged.");
  if (!opts.purge) {
    out(
      "  • Your ~/.jarvis settings are kept — remove them too with: jarvis uninstall --purge",
    );
  }
  out("  • Reinstall: re-run the curl …/install.sh command for your server.");
}

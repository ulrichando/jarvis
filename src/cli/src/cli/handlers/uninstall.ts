/**
 * `jarvis uninstall` — self-uninstall the CLI from THIS computer.
 *
 * The idiomatic pattern (rustup `self uninstall`, Claude Code's manual removal):
 * the installed binary removes itself + revokes this machine's gateway login.
 * CLIENT-SIDE ONLY — it never contacts the server, so the gateway, web app, and
 * provider keys on the VPS stay live for every other device. Config in
 * ~/.jarvis is preserved unless --purge (matching Claude Code keeping ~/.claude).
 */
import { lstatSync, rmSync } from "node:fs";
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
  opts: { purge?: boolean } = {},
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

  // 3) --purge: also remove user config/data.
  if (opts.purge) {
    try {
      rmSync(join(home, ".jarvis"), { recursive: true, force: true });
      out("  ✓ removed ~/.jarvis (keys, settings, conversations)");
    } catch {
      /* ignore */
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

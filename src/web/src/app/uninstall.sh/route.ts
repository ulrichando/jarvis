/**
 * GET /uninstall.sh — the `curl -fsSL https://0wlan.com/uninstall.sh | bash`
 * CLIENT uninstaller. Symmetric with /install.sh.
 *
 * Removes the local `jarvis` binary and revokes THIS machine's gateway login
 * (clears the local token in ~/.jarvis/keys.env). It is CLIENT-SIDE ONLY: it
 * never contacts the server, so the gateway, web app, and provider keys on the
 * VPS are untouched — uninstalling one computer leaves the deployment and every
 * other client fully live. By design it does NOT delete ~/.jarvis settings
 * (the user can remove those manually); it only undoes what /install.sh did.
 *
 * Public (allowlisted in proxy.ts + excluded from Cloudflare Access) — curl
 * can't carry a session cookie. The base is derived from the request origin so
 * the same endpoint works on localhost (dev) and the real domain (prod).
 */

function uninstallScript(base: string): string {
  return `#!/usr/bin/env bash
# JARVIS CLI uninstaller — removes the binary + this machine's login.
# CLIENT-SIDE ONLY: the server (gateway, web app, API keys) is never touched.
#   curl -fsSL ${base}/uninstall.sh | bash
set -uo pipefail

BIN_DIR="\${JARVIS_BIN_DIR:-$HOME/.local/bin}"

c_g() { printf '\\033[32m%s\\033[0m\\n' "$*"; }
c_y() { printf '\\033[33m%s\\033[0m\\n' "$*"; }

c_g "Uninstalling JARVIS CLI from this computer (the server is left untouched)"

# 1) Revoke THIS machine's gateway login. Local-only: it just clears the token
#    from ~/.jarvis/keys.env — the server is not contacted, other devices keep
#    working.
if command -v jarvis >/dev/null 2>&1; then
  jarvis auth logout >/dev/null 2>&1 && c_g "  ✓ signed out (local token cleared)" || c_y "  ⚠ sign-out skipped"
fi

# 2) Remove the installed binary(ies) this machine got from /install.sh.
removed=0
for f in "$BIN_DIR/jarvis" "$BIN_DIR/jarvis-desktop"; do
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f" && { c_g "  ✓ removed $f"; removed=1; }; fi
done
[ "$removed" = 1 ] || c_y "  (no jarvis binary found in $BIN_DIR)"

echo
c_g "Done — JARVIS CLI removed from this computer."
echo "  • The server (gateway, web app, your API keys) is unchanged."
echo "  • Your ~/.jarvis settings are kept — remove them too with:  rm -rf ~/.jarvis"
echo "  • Reinstall anytime:  curl -fsSL ${base}/install.sh | bash"
`;
}

export async function GET(req: Request): Promise<Response> {
  const origin = process.env.JARVIS_INSTALL_BASE ?? new URL(req.url).origin;
  return new Response(uninstallScript(origin), {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

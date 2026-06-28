/**
 * GET /install.sh — the `curl -fsSL https://0wlan.com/install.sh | bash` installer.
 *
 * Returns a self-contained bash script that downloads the prebuilt `jarvis`
 * binary for the user's platform from /releases/ (served by the sibling
 * releases route), installs it to ~/.local/bin, and points them at
 * `jarvis auth login` for the browser-based token mint. Mirrors claude-code's
 * install model but flows entirely through THIS web app instead of Anthropic's
 * bucket.
 *
 * Public (allowlisted in proxy.ts) — curl can't carry a session cookie.
 *
 * The download base is derived from the request origin so the SAME endpoint
 * serves a working script on localhost (dev) and the real domain (prod) with
 * no hardcoded host. JARVIS_INSTALL_BASE overrides it if the public origin
 * differs from what the request sees (e.g. behind a proxy that rewrites Host).
 */

function installScript(base: string): string {
  return `#!/usr/bin/env bash
# JARVIS CLI installer — downloads a prebuilt binary, no repo clone, no bun.
#   curl -fsSL ${base}/install.sh | bash
set -euo pipefail

BASE="\${JARVIS_INSTALL_BASE:-${base}}"
BIN_DIR="\${JARVIS_BIN_DIR:-$HOME/.local/bin}"

c_g() { printf '\\033[32m%s\\033[0m\\n' "$*"; }
c_y() { printf '\\033[33m%s\\033[0m\\n' "$*"; }
c_r() { printf '\\033[31m%s\\033[0m\\n' "$*" >&2; }
die() { c_r "✗ $*"; exit 1; }

# ── Detect platform ──────────────────────────────────────────────────────
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch=x64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) die "unsupported architecture: $arch" ;;
esac
case "$os" in
  linux|darwin) ;;
  *) die "unsupported OS: $os (jarvis CLI supports linux + macOS)" ;;
esac
asset="jarvis-\${os}-\${arch}"

c_g "Installing JARVIS CLI ($asset) from $BASE"

# ── Download ─────────────────────────────────────────────────────────────
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
url="$BASE/releases/$asset"
c_y "  ↓ $url"
if ! curl -fSL --proto '=https' --tlsv1.2 -o "$tmp/jarvis" "$url" 2>/dev/null \\
   && ! curl -fSL -o "$tmp/jarvis" "$url"; then
  die "download failed: $url (is a build published for $asset?)"
fi

# Optional integrity check against the manifest's sha256. Flatten whitespace
# first so the asset→sha256 match works regardless of the manifest's JSON
# pretty-printing (asset name and sha256 land on separate lines otherwise, and
# grep is line-based — which would silently skip verification).
if command -v sha256sum >/dev/null 2>&1; then
  want="$(curl -fsSL "$BASE/releases/manifest.json" 2>/dev/null \\
    | tr -d '[:space:]' \\
    | grep -oE "\\"$asset\\":\\{\\"sha256\\":\\"[a-f0-9]{64}\\"" \\
    | grep -oE '[a-f0-9]{64}' | head -1 || true)"
  if [ -n "$want" ]; then
    got="$(sha256sum "$tmp/jarvis" | cut -d' ' -f1)"
    [ "$got" = "$want" ] || die "checksum mismatch (got $got, want $want) — aborting"
    c_g "  ✓ checksum verified"
  fi
fi

# ── Install ──────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
install -m 0755 "$tmp/jarvis" "$BIN_DIR/jarvis"
c_g "  ✓ installed $BIN_DIR/jarvis"

ver="$("$BIN_DIR/jarvis" --version 2>/dev/null || echo '?')"
c_g "  ✓ jarvis $ver"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) c_y "  ⚠ $BIN_DIR is not on your PATH — add to your shell rc:"
     c_y "      export PATH=\\"$BIN_DIR:\\$PATH\\"" ;;
esac

echo
c_g "Done. Next:"
echo "  jarvis auth login     # opens $BASE in your browser to sign in"
echo "  jarvis                # start the assistant"
`;
}

export async function GET(req: Request): Promise<Response> {
  const origin = process.env.JARVIS_INSTALL_BASE ?? new URL(req.url).origin;
  return new Response(installScript(origin), {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

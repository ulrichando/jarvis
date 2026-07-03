// Shared redirect-target validation for the extension authorize flow. Used by
// BOTH the page (early reject) and the mint server action (defense in depth).

// The redirect target must be a Chrome extension's own chromiumapp.org callback
// — never an arbitrary URL. launchWebAuthFlow only returns the URL to the
// extension that opened it, and the <id> subdomain encodes that extension.
export function isExtensionCallback(uri: string): boolean {
  try {
    const u = new URL(uri);
    return u.protocol === "https:" && u.hostname.endsWith(".chromiumapp.org");
  } catch {
    return false;
  }
}

// Optional hard pin to specific extension IDs (JARVIS_EXTENSION_IDS, comma-
// separated). When set, only those extensions may pair — the recommended prod
// posture once the extension has a stable published ID. When UNSET (e.g. an
// unpacked dev build whose ID isn't fixed yet), the explicit consent click on
// the web page is the gate, so we don't wildcard-trust silently.
export function isAllowedExtension(uri: string): boolean {
  const ids = (process.env.JARVIS_EXTENSION_IDS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (ids.length === 0) return true;
  try {
    const id = new URL(uri).hostname.replace(/\.chromiumapp\.org$/, "");
    return ids.includes(id);
  } catch {
    return false;
  }
}

/**
 * Open-redirect guard for post-login `next` targets.
 *
 * Why stripping comes FIRST: the WHATWG URL parser — which Next's
 * `router.push` ultimately feeds navigations through (`new URL(...)`) —
 * silently REMOVES tab (\t), LF (\n) and CR (\r) anywhere in the input.
 * So `"/\t//evil.com"` passes a naive `startsWith("//")` check (char 2 is
 * a tab), but the parser then turns it into `"//evil.com"` — a
 * protocol-relative URL that hard-navigates off-origin. Validating the
 * raw string is validating the wrong string; we must validate what the
 * parser will actually see. We strip all C0 controls plus DEL (a
 * superset of \t\n\r — none are legitimate in a redirect path) and only
 * then apply the same-origin-relative-path rules.
 *
 * Pure and SSR-safe: no window/DOM.
 */
export function safeNextPath(raw: string | null | undefined): string {
  const FALLBACK = "/chat";
  if (!raw) return FALLBACK;

  // 1. Remove every char the URL parser would drop / that enables
  //    smuggling: C0 controls (U+0000–U+001F, incl. \t \n \r) and DEL.
  const cleaned = raw.replace(/[\u0000-\u001f\u007f]/g, "");
  if (!cleaned) return FALLBACK;

  // 2. Same-origin relative path only: must start with exactly one "/".
  //    Rejects absolute URLs ("https://evil.com"), scheme tricks
  //    ("javascript:alert(1)"), and protocol-relative "//host".
  if (!cleaned.startsWith("/") || cleaned.startsWith("//")) return FALLBACK;

  // 3. Defense-in-depth: no backslashes anywhere. Browsers normalize "\"
  //    to "/" in URLs, so "/\evil.com" is another protocol-relative
  //    spelling; no legitimate app path contains "\".
  if (cleaned.includes("\\")) return FALLBACK;

  return cleaned;
}

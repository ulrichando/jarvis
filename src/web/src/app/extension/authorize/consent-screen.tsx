"use client";

import { useState, type CSSProperties } from "react";
import { mintExtensionToken } from "./actions";

/**
 * The account-consent screen for "Jarvis for Chrome". Mirrors Claude for
 * Chrome's OAuth page: nothing is minted until the user clicks Authorize. That
 * click invokes a same-origin server action; the returned token is handed to
 * the extension via its chromiumapp.org callback fragment. Decline hands back
 * an error so the extension knows it was refused.
 */
export function ConsentScreen({
  redirectUri,
  email,
}: {
  redirectUri: string;
  email: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function authorize() {
    setBusy(true);
    setError(null);
    const res = await mintExtensionToken(redirectUri);
    if (!res.ok) {
      setBusy(false);
      setError(res.error);
      return;
    }
    window.location.replace(
      `${redirectUri}#token=${encodeURIComponent(res.token)}&email=${encodeURIComponent(res.email)}`,
    );
  }

  function decline() {
    window.location.replace(`${redirectUri}#error=access_denied`);
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <svg style={S.logo} viewBox="0 0 108 108" aria-hidden="true">
          <circle cx="54" cy="54" r="30" fill="none" stroke="#00C6E0" strokeWidth="3.5" />
          <circle cx="54" cy="54" r="19" fill="none" stroke="#00C6E0" strokeWidth="2" />
          <circle cx="54" cy="54" r="11" fill="none" stroke="#00C6E0" strokeWidth="1.2" />
          <circle cx="54" cy="54" r="5.5" fill="#00C6E0" />
        </svg>

        <h1 style={S.h1}>
          <b style={S.b}>Jarvis for Chrome</b> would like to connect to your Jarvis account
        </h1>

        <div style={S.panel}>
          <h2 style={S.h2}>This connection will let Jarvis</h2>
          <Bullet>Connect to your Jarvis assistant on this device</Bullet>
          <Bullet>See and read the pages you&rsquo;re viewing</Bullet>
          <Bullet>Act on web pages you approve &mdash; click, type, navigate</Bullet>
        </div>

        {error && <p style={S.err}>{error}</p>}

        <div style={S.actions}>
          <button style={{ ...S.btn, ...S.primary, ...(busy ? S.disabled : null) }} onClick={authorize} disabled={busy}>
            {busy ? "Connecting…" : "Authorize"}
          </button>
          <button style={{ ...S.btn, ...S.ghost }} onClick={decline} disabled={busy}>
            Decline
          </button>
        </div>
      </div>

      <p style={S.foot}>
        {email ? <>Signed in as <span style={S.host}>{email}</span></> : "Signed in"}
      </p>
    </div>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <div style={S.bullet}>
      <svg width="17" height="17" viewBox="0 0 20 20" fill="none" style={{ flex: "none", marginTop: 2, color: "#00C6E0" }}>
        <path d="M4 10.5l4 4 8-9" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>{children}</span>
    </div>
  );
}

const S: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 32, padding: "48px 20px", background: "oklch(0.11 0.014 252)", color: "oklch(0.97 0.007 240)", fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif" },
  card: { width: "100%", maxWidth: 540, background: "oklch(0.155 0.022 250)", border: "1px solid oklch(0.75 0.10 200 / 12%)", borderRadius: 20, padding: "40px 40px 32px", boxShadow: "0 24px 60px -30px oklch(0 0 0 / 80%)" },
  logo: { width: 60, height: 60, margin: "4px auto 26px", display: "block", filter: "drop-shadow(0 0 18px oklch(0.80 0.155 198 / 30%))" },
  h1: { margin: "0 0 26px", textAlign: "center", fontSize: 21, fontWeight: 500, lineHeight: 1.4, color: "oklch(0.74 0.032 235)" },
  b: { color: "oklch(0.97 0.007 240)", fontWeight: 650 },
  panel: { background: "oklch(0.185 0.022 250)", border: "1px solid oklch(0.75 0.10 200 / 12%)", borderRadius: 14, padding: "20px 22px" },
  h2: { margin: "0 0 16px", fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "oklch(0.74 0.032 235)" },
  bullet: { display: "flex", alignItems: "flex-start", gap: 12, fontSize: 14.5, marginTop: 14 },
  actions: { marginTop: 28, display: "grid", gap: 6 },
  btn: { font: "inherit", cursor: "pointer", borderRadius: 10, border: "1px solid transparent", padding: "12px 16px", fontSize: 15 },
  primary: { background: "oklch(0.80 0.155 198)", color: "oklch(0.10 0.012 252)", fontWeight: 650 },
  ghost: { background: "transparent", color: "oklch(0.74 0.032 235)", fontSize: 14.5 },
  disabled: { opacity: 0.6, cursor: "default" },
  err: { color: "oklch(0.68 0.19 25)", fontSize: 13, textAlign: "center", margin: "16px 0 0" },
  foot: { textAlign: "center", fontSize: 12.5, color: "oklch(0.74 0.032 235)" },
  host: { color: "oklch(0.80 0.155 198 / 85%)" },
};

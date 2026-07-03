import { redirect } from "next/navigation";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { isExtensionCallback, isAllowedExtension } from "./validate";
import { ConsentScreen } from "./consent-screen";

/**
 * /extension/authorize — the Chrome extension's "Log in to your Jarvis account"
 * target. Opened via chrome.identity.launchWebAuthFlow with redirect_uri = the
 * extension's https://<id>.chromiumapp.org/ callback.
 *
 * Security: this page NEVER mints on load. It validates the redirect target
 * (must be a chromiumapp.org callback, and — when JARVIS_EXTENSION_IDS is set —
 * a pinned extension id), requires a signed-in session, then renders an
 * explicit Authorize/Decline consent. Only the Authorize click (a same-origin
 * server action) mints the per-user proxy JWT. A passive visit mints nothing.
 */
export default async function ExtensionAuthorizePage({
  searchParams,
}: {
  searchParams: Promise<{ redirect_uri?: string | string[] }>;
}) {
  const search = await searchParams;
  const raw = search?.redirect_uri;
  const redirectUri = Array.isArray(raw) ? raw[0] : raw;

  if (!redirectUri || !isExtensionCallback(redirectUri) || !isAllowedExtension(redirectUri)) {
    return <Notice>This connection request is invalid or from an unrecognized extension.</Notice>;
  }

  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    // Not signed in — bounce to login, returning here (with the redirect_uri).
    redirect(
      `/login?next=${encodeURIComponent(
        `/extension/authorize?redirect_uri=${encodeURIComponent(redirectUri)}`,
      )}`,
    );
  }

  return <ConsentScreen redirectUri={redirectUri} email={session.user.email ?? ""} />;
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "oklch(0.11 0.014 252)",
        color: "oklch(0.97 0.007 240)",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      }}
    >
      <p style={{ maxWidth: 320, textAlign: "center", padding: 24, fontSize: 14 }}>
        {children}
      </p>
    </div>
  );
}

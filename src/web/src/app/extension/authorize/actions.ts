"use server";

import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";
import { signProxyToken } from "@/lib/bridge/proxyJwt";
import { isExtensionCallback, isAllowedExtension } from "./validate";

// 30 days — matches the session lifetime + the /api/bridge/proxy-token route.
const TTL_SECONDS = 60 * 60 * 24 * 30;

/**
 * Mint the per-user proxy JWT for the Chrome extension. Called ONLY from the
 * consent screen's Authorize button — a same-origin server action (Next binds
 * the action + enforces same-origin POST), so a passive page visit or a
 * cross-origin/extension caller cannot trigger it. Re-validates the redirect
 * target and the session here too (never trust the client round-trip).
 */
export async function mintExtensionToken(
  redirectUri: string,
): Promise<{ ok: true; token: string; email: string } | { ok: false; error: string }> {
  if (!isExtensionCallback(redirectUri) || !isAllowedExtension(redirectUri)) {
    return { ok: false, error: "Unrecognized extension." };
  }
  const session = await auth.api.getSession({ headers: await headers() });
  const userId = session?.user?.id;
  if (!userId) return { ok: false, error: "Your session expired — sign in again." };

  const token = signProxyToken(
    { sub: userId, ttlSeconds: TTL_SECONDS },
    getOrCreateProxyJwtSecret(),
  );
  return { ok: true, token, email: session.user.email ?? "" };
}

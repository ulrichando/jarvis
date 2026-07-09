"use client";

import { createAuthClient } from "better-auth/react";
import { twoFactorClient } from "better-auth/client/plugins";

// Same-origin client (baseURL defaults to window.location.origin).
//
// twoFactorClient is deliberately configured WITHOUT `onTwoFactorRedirect` /
// `twoFactorPage`: those hooks exist for redirect-to-a-dedicated-2FA-page
// flows (twoFactorPage even forces a full reload). The login page instead
// detects `{ twoFactorRedirect: true }` on the `signIn.email` response and
// renders the TOTP / backup-code challenge inline — see
// app/(auth)/login/page.tsx. If another sign-in entry point is ever added,
// it must handle `twoFactorRedirect` too (or wire the global hook here).
export const authClient = createAuthClient({
  plugins: [twoFactorClient()],
});

export const { signIn, signUp, signOut, useSession } = authClient;

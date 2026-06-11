"use client";

import { createAuthClient } from "better-auth/react";

// Same-origin client (baseURL defaults to window.location.origin).
export const authClient = createAuthClient();

export const { signIn, signUp, signOut, useSession } = authClient;

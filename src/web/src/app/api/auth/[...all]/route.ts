import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";

// better-auth catch-all: sign-up/email, sign-in/email, sign-out, get-session, …
export const { GET, POST } = toNextJsHandler(auth);

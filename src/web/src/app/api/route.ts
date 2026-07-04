import { NextResponse } from "next/server";

export const runtime = "nodejs";

// GET /api — an index for the API namespace. Without a handler here, bare
// `/api` is a raw 404 (Next treats app/api/* as route handlers, so the page
// not-found.tsx never catches it). This returns a small, public, no-secrets
// descriptor so the path resolves and points callers at what they need.
export function GET(): NextResponse {
  return NextResponse.json({
    name: "Jarvis API",
    status: "ok",
    endpoints: {
      health: "/api/health",
    },
    // Most routes live under /api/bridge/* and require a bearer token — mint one
    // in Settings → API Tokens.
    auth: "Bearer token from Settings → API Tokens",
    app: "https://0wlan.com",
  });
}

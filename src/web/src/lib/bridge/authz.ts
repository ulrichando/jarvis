import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findEnvironment, findSession, validateSessionToken } from "@/lib/bridge/store";
import { authorizeSessionCredential, extractBearer } from "@/lib/bridge/auth";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// ── Unified bridge session-scope authorization (finding #10) ──────────────────
//
// The ~40 bridge routes hand-rolled the `extractBearer → resolveBridgeToken /
// validateEnvSecret / isSharedLocalToken / cookie-ownership` ladder inline, and
// the same fail-open class was patched route-by-route ≥3× (the presence-only
// bearer check that findings #2/#3 closed). This collapses the SESSION-scoped
// gates into one entry point so the accept/reject contract lives in exactly one
// place. Four scopes, each matching an existing hand-rolled gate byte-for-byte:
//
//   "session-owner"      — a bearer of ANY shape passes (v1-permissive: the
//                          proxy's shared-token bearer gate already fronts these
//                          mutation/read-meta routes and establishes "trusted
//                          local caller"); with no bearer, the browser cookie
//                          must own the session's environment. Mirrors the
//                          inline `authorizeMutation`/`authorizeSession` copies
//                          in diff/plan/pr-status/pins/pr/sessions[id].
//   "session-read"       — a bearer must be a VALID session credential (ingress
//                          token / env secret / owning per-user token / shared
//                          infra token); with no bearer, the cookie must own the
//                          environment. Transcript read (events GET) — junk
//                          bearers can't fall through to the cookie path.
//   "session-credential" — a bearer must be a VALID session credential; there is
//                          NO cookie path. Worker append/archive (events POST,
//                          archive POST).
//   "session-token"      — a bearer must be the session's ingress token exactly.
//                          The CCR v2 /code worker routes.
//
// Returns a NextResponse to short-circuit on, or null when the request is
// authorized. Kept as one function so a future 5th scope is a switch arm, not a
// 5th copy of the ladder.
export type BridgeScope =
  | "session-owner"
  | "session-read"
  | "session-credential"
  | "session-token";

export async function authorizeBridgeRequest(
  req: Request,
  opts: { scope: BridgeScope; sessionId: string },
): Promise<NextResponse | null> {
  const { scope, sessionId } = opts;
  const store = getStore();
  const token = extractBearer(req.headers.get("authorization"));

  switch (scope) {
    case "session-owner": {
      // Any bearer passes (v1-permissive). No bearer → cookie ownership.
      if (token) return null;
      const session = findSession(store, sessionId);
      if (!session) return bridgeError(404, "not_found", "Session not found");
      const env = session.environment_id
        ? findEnvironment(store, session.environment_id)
        : null;
      const userId = await getUserId(req.headers);
      if (env?.user_id && env.user_id !== userId) {
        // No valid session (getUserId → null) against a real-owned session is
        // "your login expired" → 401 (re-login), not a dead-end 403. A genuine
        // cross-user mismatch (two real accounts) still returns 403.
        if (userId === null) {
          return bridgeError(401, "unauthenticated", "Session expired — please sign in again");
        }
        return bridgeError(403, "forbidden", "Not your session");
      }
      return null;
    }

    case "session-read": {
      if (token) {
        // A bearer was presented: it must be a valid session credential. Don't
        // fall through to the cookie path on a junk bearer (a non-browser
        // caller controls both).
        return authorizeSessionCredential(store, sessionId, token)
          ? null
          : bridgeError(401, "unauthorized", "Invalid session credential");
      }
      // No bearer → browser same-origin cookie path (the /code poller). Resolve
      // the logged-in user and require it to own the session's environment.
      const session = findSession(store, sessionId);
      if (!session) return bridgeError(404, "not_found", "Session not found");
      const userId = await getUserId(req.headers);
      if (!userId) {
        return bridgeError(401, "unauthenticated", "Sign in to read this session");
      }
      const env = session.environment_id
        ? findEnvironment(store, session.environment_id)
        : null;
      // A session bound to an owned environment is readable only by its owner.
      // An ownerless / unbound (orphan) env stays readable by any logged-in
      // user, matching authorizeSessionCredential's orphan semantics.
      if (env?.user_id && env.user_id !== userId) {
        return bridgeError(403, "forbidden", "Not your session");
      }
      return null;
    }

    case "session-credential": {
      // Worker append/archive: a valid session credential is REQUIRED; no
      // cookie path. On the proxy's SELF_AUTH allowlist so the remote-control
      // worker reaches it online, which means junk bearers must be rejected
      // here (replaces the pre-#2 "any non-empty bearer" rule).
      if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
      return authorizeSessionCredential(store, sessionId, token)
        ? null
        : bridgeError(401, "unauthorized", "Invalid session credential");
    }

    case "session-token": {
      // The CCR v2 /code worker routes: the bearer is the per-session ingress
      // token minted at session creation.
      if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
      if (!validateSessionToken(store, sessionId, token)) {
        return bridgeError(401, "unauthorized", "Invalid session token");
      }
      return null;
    }
  }
}

// Back-compat thin wrapper for the "session-owner" scope. All in-tree bridge
// routes now call authorizeBridgeRequest directly; this delegator is retained
// so any out-of-tree caller keeps its signature. The CLI worker presents a
// bearer (v1-permissive — any non-empty token); the browser presents a
// same-origin session cookie, checked against the session's owning environment.
export async function authorizeSession(
  req: Request,
  sessionId: string,
): Promise<NextResponse | null> {
  return authorizeBridgeRequest(req, { scope: "session-owner", sessionId });
}

// Back-compat thin wrapper for the "session-read" scope (GET
// /api/bridge/v1/sessions/{id}/events transcript read). Accepts a VALID session
// credential bearer OR the browser same-origin cookie owning the session's
// environment — see the "session-read" arm of authorizeBridgeRequest for the
// full rationale (the IDOR hole finding #2 closed). The events GET now calls
// authorizeBridgeRequest directly; this delegator is retained for any
// out-of-tree caller.
export async function authorizeSessionRead(
  req: Request,
  sessionId: string,
): Promise<NextResponse | null> {
  return authorizeBridgeRequest(req, { scope: "session-read", sessionId });
}

// Worker session-token gate for the CCR v2 /code worker routes: the bearer is
// the per-session ingress token minted at session creation. Extracted from the
// 7 worker routes that each repeated this exact check (2 as a local `authorize`,
// 5 inlined). Returns an error response to short-circuit on, or null when
// allowed. Distinct from authorizeSession above (the browser cookie-ownership
// gate); this is the token-presenting worker's gate.
export function authorizeSessionToken(
  req: Request,
  sessionId: string,
): NextResponse | null {
  // Synchronous wrapper: the "session-token" scope never awaits (no cookie
  // path), so the promise resolves in the same microtask — but the worker
  // routes call this synchronously (`const denied = authorizeSessionToken(...)`),
  // so keep the sync signature by inlining the (non-async) check here rather
  // than unwrapping the async unified entry point.
  const token = extractBearer(req.headers.get("authorization"));
  if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
  if (!validateSessionToken(getStore(), sessionId, token)) {
    return bridgeError(401, "unauthorized", "Invalid session token");
  }
  return null;
}

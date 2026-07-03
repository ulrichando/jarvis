// @vitest-environment node
import { beforeAll, describe, expect, it } from "vitest";
import type { NextRequest } from "next/server";
import { NextRequest as NextRequestCtor } from "next/server";

// The proxy reads env at module load, so pin the dev-mode config (bearer gate
// OFF) BEFORE importing it: the CSRF hard-stop must fire even when
// JARVIS_REQUIRE_LOCAL_AUTH is unset — that's the dev-mode drive-by that let a
// cross-site page POST /api/workspace/<id>/exec (arbitrary container shell).
delete process.env.JARVIS_REQUIRE_LOCAL_AUTH;
process.env.JARVIS_AUTH_DISABLED = "1"; // don't redirect page routes (N/A here)

let proxy: (req: NextRequest) => Response;
beforeAll(async () => {
  ({ proxy } = (await import("@/proxy")) as unknown as {
    proxy: (req: NextRequest) => Response;
  });
});

function req(method: string, path: string, secFetchSite: string): NextRequest {
  return new NextRequestCtor(`http://127.0.0.1:3000${path}`, {
    method,
    headers: { host: "127.0.0.1:3000", "sec-fetch-site": secFetchSite },
  });
}

describe("proxy CSRF hard-stop (dev-mode drive-by defense)", () => {
  it("blocks a cross-site state-changing /api/* request → 403", async () => {
    const res = await proxy(req("POST", "/api/workspace/abc/exec", "cross-site"));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "cross-site request refused" });
  });

  it("allows the real same-origin UI write", async () => {
    const res = await proxy(req("POST", "/api/workspace/abc/exec", "same-origin"));
    expect(res.status).not.toBe(403);
  });

  it("does not block cross-site GETs (only state-changing methods)", async () => {
    const res = await proxy(req("GET", "/api/workspace/abc/file", "cross-site"));
    expect(res.status).not.toBe(403);
  });

  it("exempts /api/auth/* (better-auth runs its own Origin/CSRF check)", async () => {
    const res = await proxy(req("POST", "/api/auth/sign-in/email", "cross-site"));
    expect(res.status).not.toBe(403);
  });
});

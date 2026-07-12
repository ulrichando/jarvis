import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/iot/[...path]/route";

// The /api/iot/* forwarder proxies GET/POST to the local IoT discovery
// sidecar (127.0.0.1:8779). Auth is proxy.ts's default /api/* gate — the
// route itself is a plain forwarder, mirroring /api/computer-use.

type Ctx = { params: Promise<{ path: string[] }> };
const ctx = (...path: string[]): Ctx => ({ params: Promise.resolve({ path }) });

function stubFetch(reply: { status?: number; body?: unknown } | { throws: true }) {
  const fn = vi.fn(async () => {
    if ("throws" in reply) throw new Error("ECONNREFUSED");
    return new Response(JSON.stringify(reply.body ?? {}), {
      status: reply.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("/api/iot/[...path] forwarder", () => {
  it("GET /api/iot/devices forwards to the sidecar and returns its JSON", async () => {
    const devices = [
      { id: "aa:bb:cc:dd:ee:ff", name: "Roku TV", type: "tv", controllable: "local" },
    ];
    const fetchMock = stubFetch({ body: { devices } });

    const res = await GET(new Request("http://localhost/api/iot/devices"), ctx("devices"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const target = String(fetchMock.mock.calls[0]![0]);
    expect(target).toBe("http://127.0.0.1:8779/devices");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ devices });
  });

  it("GET forwards the query string (controllable filter)", async () => {
    const fetchMock = stubFetch({ body: { devices: [] } });
    await GET(
      new Request("http://localhost/api/iot/devices?controllable=local"),
      ctx("devices"),
    );
    expect(String(fetchMock.mock.calls[0]![0])).toBe(
      "http://127.0.0.1:8779/devices?controllable=local",
    );
  });

  it("POST /api/iot/scan forwards the method and returns the scan result", async () => {
    const fetchMock = stubFetch({ body: { devices: [{ name: "Hue Bridge" }] } });

    const res = await POST(
      new Request("http://localhost/api/iot/scan", { method: "POST" }),
      ctx("scan"),
    );

    const [target, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(String(target)).toBe("http://127.0.0.1:8779/scan");
    expect(init.method).toBe("POST");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ devices: [{ name: "Hue Bridge" }] });
  });

  it("POST forwards a JSON body and passes the upstream status through (command → 501)", async () => {
    const fetchMock = stubFetch({
      status: 501,
      body: { error: "control not implemented (Phase 1 discovery-only)" },
    });

    const res = await POST(
      new Request("http://localhost/api/iot/devices/aa:bb/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "on" }),
      }),
      ctx("devices", "aa:bb", "command"),
    );

    const [target, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(String(target)).toBe("http://127.0.0.1:8779/devices/aa%3Abb/command");
    expect(JSON.parse(String(init.body))).toEqual({ action: "on" });
    expect(res.status).toBe(501);
    const body = (await res.json()) as { error: string };
    expect(body.error).toMatch(/not implemented/);
  });

  it("sidecar unreachable → 502 with a helpful hint, no throw", async () => {
    stubFetch({ throws: true });
    const res = await GET(new Request("http://localhost/api/iot/devices"), ctx("devices"));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string };
    expect(body.error).toMatch(/IoT/i);
  });
});

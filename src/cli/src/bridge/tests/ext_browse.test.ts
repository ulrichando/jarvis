import { test, expect, describe } from "bun:test";
import { handleExtBrowse, registerExtensionWS, _resetForTests } from "../ext_browse";

describe("ext_browse", () => {
  test("returns 503 when no extension connected", async () => {
    _resetForTests();
    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "https://example.com"}}),
      headers: {"content-type": "application/json"},
    });
    const res = await handleExtBrowse(req);
    expect(res.status).toBe(503);
  });

  test("queues command when extension connected, resolves on WS reply", async () => {
    _resetForTests();
    const fakeWS = {
      sent: [] as any[],
      send(data: string) { this.sent.push(JSON.parse(data)); },
      readyState: 1,
    };
    registerExtensionWS(fakeWS as any);

    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "https://example.com"}}),
      headers: {"content-type": "application/json"},
    });
    const resPromise = handleExtBrowse(req);

    await new Promise(r => setTimeout(r, 50));
    expect(fakeWS.sent.length).toBe(1);
    const cmd_id = fakeWS.sent[0].cmd_id;

    const { resolveExtensionResponse } = await import("../ext_browse");
    resolveExtensionResponse({cmd_id, ok: true, page_state: {url: "https://example.com"}});

    const res = await resPromise;
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.page_state.url).toBe("https://example.com");
  });

  test("times out after configured ms with 504", async () => {
    _resetForTests();
    const fakeWS = { sent: [] as any[], send(d: string){ this.sent.push(JSON.parse(d)); }, readyState: 1 };
    registerExtensionWS(fakeWS as any);
    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "x"}, timeout_ms: 100}),
      headers: {"content-type": "application/json"},
    });
    const res = await handleExtBrowse(req);
    expect(res.status).toBe(504);
  });
});

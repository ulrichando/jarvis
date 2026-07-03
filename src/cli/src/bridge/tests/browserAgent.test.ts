import { test, expect, describe, afterEach } from "bun:test";
import { runBrowserAgent } from "../browserAgent";

// Stub the LLM proxy: return the queued responses in order (last one repeats).
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });
function stubProxy(responses: any[]) {
  let i = 0;
  globalThis.fetch = (async () => {
    const body = responses[Math.min(i, responses.length - 1)];
    i++;
    return { json: async () => body } as any;
  }) as any;
}
const toolUse = (name: string, input: any) => ({ content: [{ type: "tool_use", id: "t1", name, input }] });
const finalText = { content: [{ type: "text", text: "done" }] };
const base = { model: "m", proxyUrl: "http://p", headers: {}, onEvent: () => {} };

describe("runBrowserAgent gating", () => {
  test("download is confirmation-gated in ask mode; denial skips the send", async () => {
    stubProxy([toolUse("download", { url: "https://x/f.zip" }), finalText]);
    const sent: any[] = [];
    const approvals: string[] = [];
    await runBrowserAgent({
      ...base, text: "grab that file", mode: "ask",
      sendCommand: async (a, args) => { sent.push({ a, args }); return { ok: true }; },
      requestApproval: async (label) => { approvals.push(label); return false; },
    });
    expect(approvals.length).toBe(1); // asked BEFORE downloading
    expect(sent.some((s) => s.a === "download")).toBe(false); // denied → never sent
  });

  test("download sends once approved in ask mode", async () => {
    stubProxy([toolUse("download", { url: "https://x/f.zip" }), finalText]);
    const sent: any[] = [];
    await runBrowserAgent({
      ...base, text: "grab it", mode: "ask",
      sendCommand: async (a, args) => { sent.push({ a, args }); return { ok: true }; },
      requestApproval: async () => true,
    });
    expect(sent.filter((s) => s.a === "download").length).toBe(1);
  });

  test("auto mode downloads without a prompt", async () => {
    stubProxy([toolUse("download", { url: "https://x/f.zip" }), finalText]);
    const sent: any[] = [];
    let asked = 0;
    await runBrowserAgent({
      ...base, text: "grab it", mode: "auto",
      sendCommand: async (a, args) => { sent.push({ a, args }); return { ok: true }; },
      requestApproval: async () => { asked++; return true; },
    });
    expect(asked).toBe(0);
    expect(sent.filter((s) => s.a === "download").length).toBe(1);
  });

  test("wait_for timeout_ms is clamped under the 15s command budget", async () => {
    stubProxy([toolUse("wait_for", { selector: "#x", timeout_ms: 30000 }), finalText]);
    const sent: any[] = [];
    await runBrowserAgent({
      ...base, text: "wait", mode: "auto",
      sendCommand: async (a, args) => { sent.push({ a, args }); return { ok: true }; },
      requestApproval: async () => true,
    });
    expect(sent.find((s) => s.a === "wait_for").args.timeout_ms).toBe(12000);
  });
});

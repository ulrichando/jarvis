import { test, expect, describe, afterEach } from "bun:test";
import { visionAnswer } from "../vision";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

describe("visionAnswer", () => {
  test("posts an Anthropic image block to the vision model + parses the reply", async () => {
    let captured: any = null;
    globalThis.fetch = (async (url: string, init: any) => {
      captured = { url, headers: init.headers, body: JSON.parse(init.body) };
      return { json: async () => ({ content: [{ type: "text", text: "a red circle and a blue square" }] }) } as any;
    }) as any;

    const out = await visionAnswer(
      "what is this?",
      { data: "BASE64DATA", media_type: "image/png" },
      { proxyUrl: "http://p", headers: { Authorization: "Bearer x" }, model: "gemini-flash" },
    );

    expect(out).toBe("a red circle and a blue square");
    expect(captured.url).toBe("http://p/v1/messages");
    expect(captured.headers.Authorization).toBe("Bearer x"); // reuses proxy auth
    expect(captured.body.model).toBe("gemini-flash");
    const content = captured.body.messages[0].content;
    // exactly the shape convert.test.ts proves the proxy converts to pixels:
    expect(content[0]).toEqual({ type: "text", text: "what is this?" });
    expect(content[1]).toEqual({ type: "image", source: { type: "base64", media_type: "image/png", data: "BASE64DATA" } });
  });

  test("empty text defaults the prompt; missing media_type defaults to png", async () => {
    let body: any = null;
    globalThis.fetch = (async (_u: string, init: any) => { body = JSON.parse(init.body); return { json: async () => ({ content: [{ type: "text", text: "ok" }] }) } as any; }) as any;
    const out = await visionAnswer("", { data: "X" }, { proxyUrl: "http://p", headers: {}, model: "m" });
    expect(out).toBe("ok");
    expect(body.messages[0].content[0].text).toBe("Describe this image.");
    expect(body.messages[0].content[1].source.media_type).toBe("image/png");
  });

  test("surfaces a proxy error instead of a blank reply", async () => {
    globalThis.fetch = (async () => ({ json: async () => ({ error: { message: "out of credits" } }) }) as any) as any;
    const err = await visionAnswer("hi", { data: "X" }, { proxyUrl: "http://p", headers: {}, model: "m" });
    expect(err).toContain("out of credits");
  });

  test("a fetch/network failure returns a readable error, not a throw", async () => {
    globalThis.fetch = (async () => { throw new Error("ECONNREFUSED"); }) as any;
    const err = await visionAnswer("hi", { data: "X" }, { proxyUrl: "http://p", headers: {}, model: "m" });
    expect(err).toContain("ECONNREFUSED");
  });
});

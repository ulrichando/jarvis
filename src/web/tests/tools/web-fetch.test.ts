import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { webFetchTool } from "../../src/lib/tools/web-fetch";

type Out = { url: string; title?: string; text?: string; error?: string };

// tool.execute has a required second arg (ToolCallOptions) we don't use —
// same cast as tests/web-search.test.ts.
const run = (url: string): Promise<Out> =>
  (webFetchTool.execute as (i: { url: string }, o: unknown) => Promise<Out>)(
    { url },
    {},
  );

// Plain Response-shaped objects (not `new Response()`): lets tests set
// normally-forbidden headers like content-length and 3xx statuses without
// fighting undici's constructor guards. web-fetch only touches
// ok/status/headers/text().
function fakeResponse(
  body: string,
  init: { status?: number; headers?: Record<string, string> } = {},
) {
  const status = init.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(init.headers ?? {}),
    text: async () => body,
  };
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("webFetchTool", () => {
  it("fetches an HTML page and returns stripped text + decoded title", async () => {
    const html = `<html><head><title>My &amp; Great Page</title></head><body>
      <script>var tracking = "should never appear";</script>
      <main><p>Hello world content that the model should read.</p>
      <p>Second paragraph with more detail.</p></main></body></html>`;
    fetchMock.mockResolvedValue(
      fakeResponse(html, { headers: { "content-type": "text/html; charset=utf-8" } }),
    );

    const out = await run("https://example.com/article");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("https://example.com/article");
    expect(out.error).toBeUndefined();
    expect(out.title).toBe("My & Great Page");
    expect(out.text).toContain("Hello world content");
    expect(out.text).toContain("Second paragraph");
    expect(out.text).not.toContain("<p>");
    expect(out.text).not.toContain("should never appear");
  });

  it("blocks private / loopback / metadata URLs WITHOUT fetching", async () => {
    for (const url of [
      "http://192.168.1.10/admin",
      "http://10.0.0.1/",
      "http://127.0.0.1:8765/bridge",
      "http://localhost:3000/api/keys",
      "http://169.254.169.254/latest/meta-data/",
      "http://searxng:8080/search", // bare in-network service name
    ]) {
      const out = await run(url);
      expect(out.error, url).toMatch(/blocked/i);
      expect(out.text, url).toBeUndefined();
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects non-http(s) schemes without fetching", async () => {
    for (const url of ["file:///etc/passwd", "ftp://example.com/x", "not a url"]) {
      const out = await run(url);
      expect(out.error, url).toMatch(/blocked/i);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks a redirect that lands on a private address", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse("", { status: 302, headers: { location: "http://127.0.0.1/secret" } }),
    );
    const out = await run("https://example.com/go");
    expect(out.error).toMatch(/redirected to a non-public address/i);
    expect(fetchMock).toHaveBeenCalledTimes(1); // never fetched the private hop
  });

  it("follows a relative redirect to a public page", async () => {
    fetchMock
      .mockResolvedValueOnce(
        fakeResponse("", { status: 301, headers: { location: "/moved" } }),
      )
      .mockResolvedValueOnce(
        fakeResponse(
          "<html><head><title>Moved</title></head><body><p>Final destination text.</p></body></html>",
          { headers: { "content-type": "text/html" } },
        ),
      );
    const out = await run("https://example.com/old");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("https://example.com/moved");
    expect(out.title).toBe("Moved");
    expect(out.text).toContain("Final destination text");
  });

  it("returns an error result on HTTP error status", async () => {
    fetchMock.mockResolvedValue(fakeResponse("nope", { status: 404 }));
    const out = await run("https://example.com/missing");
    expect(out.error).toBe("HTTP 404");
    expect(out.text).toBeUndefined();
  });

  it("returns an error result on timeout (never throws)", async () => {
    fetchMock.mockRejectedValue(
      Object.assign(new Error("The operation timed out"), { name: "TimeoutError" }),
    );
    const out = await run("https://slow.example.com/");
    expect(out.error).toMatch(/timed out/i);
  });

  it("returns an error result on network failure (never throws)", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));
    const out = await run("https://down.example.com/");
    expect(out.error).toMatch(/fetch failed/i);
  });

  it("rejects oversize responses via content-length before buffering", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse("x", {
        headers: { "content-type": "text/html", "content-length": "6000000" },
      }),
    );
    const out = await run("https://example.com/huge");
    expect(out.error).toMatch(/too large/i);
  });

  it("returns a PDF-not-supported note for application/pdf", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse("%PDF-1.4 binary soup", {
        headers: { "content-type": "application/pdf" },
      }),
    );
    const out = await run("https://example.com/paper.pdf");
    expect(out.error).toMatch(/PDF text extraction isn't available/i);
    expect(out.text).toBeUndefined();
  });

  it("truncates long pages at a word boundary around 10k chars", async () => {
    const html = `<html><body><article><p>${"lengthy word salad ".repeat(2000)}</p></article></body></html>`;
    fetchMock.mockResolvedValue(
      fakeResponse(html, { headers: { "content-type": "text/html" } }),
    );
    const out = await run("https://example.com/long");
    expect(out.error).toBeUndefined();
    expect(out.text!.length).toBeLessThanOrEqual(10_001); // 10k + ellipsis
    expect(out.text!.length).toBeGreaterThan(9_000);
    expect(out.text!.endsWith("…")).toBe(true);
  });

  it("passes through text/plain bodies (no title) with the same cap", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse("Plain readme line one.\nLine two.", {
        headers: { "content-type": "text/plain" },
      }),
    );
    const out = await run("https://example.com/README");
    expect(out.error).toBeUndefined();
    expect(out.title).toBe("");
    expect(out.text).toContain("Plain readme line one.");
  });
});

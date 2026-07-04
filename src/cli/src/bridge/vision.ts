// vision.ts — image Q&A for the extension's "+ → Take a screenshot / Add an
// image". Posts the image to a supportsVision model via the proxy, which
// converts the Anthropic image block to pixels (deepseek-v4-pro, the agent pin,
// can't see images). Plain completion, NOT the tool loop. Extracted from
// server.ts so the request shape + parsing are unit-testable without the proxy.

export interface VisionOpts {
  proxyUrl: string;
  headers: Record<string, string>;
  model: string;
}

export async function visionAnswer(
  text: string,
  image: { data: string; media_type?: string },
  opts: VisionOpts,
): Promise<string> {
  try {
    const resp = await fetch(`${opts.proxyUrl}/v1/messages`, {
      method: "POST",
      headers: opts.headers,
      body: JSON.stringify({
        model: opts.model,
        max_tokens: 1200,
        messages: [{ role: "user", content: [
          { type: "text", text: text || "Describe this image." },
          { type: "image", source: { type: "base64", media_type: image.media_type || "image/png", data: image.data } },
        ] }],
      }),
      signal: AbortSignal.timeout(45_000),
    });
    const data: any = await resp.json();
    if (data?.error) return `Error: ${data.error.message || JSON.stringify(data.error)}`;
    const texts = (Array.isArray(data?.content) ? data.content : [])
      .filter((b: any) => b.type === "text").map((b: any) => b.text).filter(Boolean);
    return texts.join("\n") || "(no response)";
  } catch (e: any) {
    return `Error reaching the vision model: ${e?.message || e}`;
  }
}

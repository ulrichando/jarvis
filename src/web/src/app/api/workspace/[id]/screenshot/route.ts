import { NextResponse } from "next/server";
import { dockerStatus } from "@/lib/workspace/docker";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

export const runtime = "nodejs";
export const maxDuration = 60;

const execFileP = promisify(execFile);

/**
 * GET /api/workspace/[id]/screenshot
 *
 * Headless-Chromium screenshot of the live dev server. We resolve the
 * container's host-mapped port for 5173, navigate Playwright to it,
 * wait for network-idle, and return a PNG.
 *
 * Why this exists: jarvis was claiming "matches the design" without
 * actually looking at the rendered output — text-only models have no
 * channel to verify visual equivalence. Multimodal models with the
 * preview screenshot in their context CAN compare. Bolt / v0 / Lovable
 * all auto-screenshot the preview after each build for this reason.
 *
 * Returns PNG body. Optional ?format=base64 returns
 * { dataUrl: "data:image/png;base64,..." } for inline embed in
 * UIMessage file parts.
 */
export async function GET(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/screenshot">,
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const format = url.searchParams.get("format"); // "base64" | null
  const route = url.searchParams.get("route") ?? "/";
  const widthRaw = parseInt(url.searchParams.get("width") ?? "1280", 10);
  const heightRaw = parseInt(url.searchParams.get("height") ?? "800", 10);
  const width = Math.min(Math.max(Number.isFinite(widthRaw) ? widthRaw : 1280, 320), 2400);
  const height = Math.min(Math.max(Number.isFinite(heightRaw) ? heightRaw : 800, 320), 2000);

  const status = await dockerStatus();
  if (!status.available || !status.imageReady) {
    return NextResponse.json(
      { error: "docker_not_ready" },
      { status: 503 },
    );
  }

  // Resolve the host port for container's 5173. `docker port` returns
  // lines like "5173/tcp -> 0.0.0.0:32768". We pick the first.
  const containerName = `jarvis-ws-${id}`;
  let hostPort: number | null = null;
  try {
    const { stdout } = await execFileP("docker", [
      "port",
      containerName,
      "5173/tcp",
    ]);
    const m = stdout.match(/:(\d+)\s*$/m);
    if (m) hostPort = parseInt(m[1], 10);
  } catch {
    /* container not running or no published port */
  }
  if (!hostPort) {
    return NextResponse.json(
      { error: "preview_not_ready", hint: "Dev server isn't listening on 5173 yet." },
      { status: 503 },
    );
  }

  const target = `http://localhost:${hostPort}${route.startsWith("/") ? route : `/${route}`}`;

  // Lazy-load playwright so cold starts of unrelated routes don't pull
  // it in. Playwright + chromium binary land at ~150MB.
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor: 1,
    });
    try {
      // 30s budget. networkidle may never resolve on apps with WS or
      // long-poll connections — fall back to "load" if it times out.
      await page
        .goto(target, { waitUntil: "networkidle", timeout: 25_000 })
        .catch(() =>
          page.goto(target, { waitUntil: "load", timeout: 5_000 }),
        );
      // Small settle so animations / fonts paint.
      await page.waitForTimeout(400);
      const png = await page.screenshot({ type: "png", fullPage: false });

      if (format === "base64") {
        const dataUrl = `data:image/png;base64,${png.toString("base64")}`;
        return NextResponse.json({
          dataUrl,
          target,
          width,
          height,
          bytes: png.byteLength,
        });
      }
      const ab = png.buffer.slice(
        png.byteOffset,
        png.byteOffset + png.byteLength,
      );
      return new Response(ab as ArrayBuffer, {
        headers: {
          "Content-Type": "image/png",
          "Cache-Control": "no-store",
          "X-Preview-Url": target,
        },
      });
    } finally {
      await page.close().catch(() => {});
    }
  } catch (err) {
    return NextResponse.json(
      { error: "screenshot_failed", message: (err as Error).message },
      { status: 500 },
    );
  } finally {
    await browser.close().catch(() => {});
  }
}

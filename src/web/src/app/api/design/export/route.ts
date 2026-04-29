import { type NextRequest, NextResponse } from "next/server";
import { chromium } from "playwright";
import { z } from "zod";
import { type Format } from "@/lib/design/format";

export const runtime = "nodejs";
export const maxDuration = 120;

const QuerySchema = z.object({
  workspaceId: z.string().min(1),
  path: z.string().min(1),
  format: z
    .enum(["slides", "prototype", "landing", "onepager", "infographic"])
    .optional(),
  output: z.enum(["pdf"]).default("pdf"),
});

type PageSize =
  | { format: "A4" | "Letter" }
  | { width: string; height: string };

function pageSizeFor(format: Format | undefined): PageSize {
  switch (format) {
    case "slides":
      return { width: "1920px", height: "1080px" };
    case "infographic":
      return { width: "1080px", height: "1920px" };
    case "prototype":
      return { width: "390px", height: "844px" };
    case "onepager":
      return { format: "A4" };
    case "landing":
    default:
      return { format: "Letter" };
  }
}

export async function GET(req: NextRequest) {
  let params;
  try {
    params = QuerySchema.parse(Object.fromEntries(req.nextUrl.searchParams));
  } catch (err) {
    return NextResponse.json(
      { error: "invalid query", detail: err instanceof Error ? err.message : String(err) },
      { status: 400 },
    );
  }

  const origin = req.nextUrl.origin;
  const fileUrl = `${origin}/api/workspace/${encodeURIComponent(
    params.workspaceId,
  )}/file?path=${encodeURIComponent(params.path)}&raw=1`;

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const resp = await page.goto(fileUrl, {
      waitUntil: "networkidle",
      timeout: 60_000,
    });
    if (!resp || !resp.ok()) {
      return NextResponse.json(
        { error: `fetch ${resp?.status() ?? "?"}` },
        { status: 502 },
      );
    }
    // Wait for fonts so Google Fonts render in the PDF.
    await page.evaluate(
      () =>
        (
          document as Document & { fonts: { ready: Promise<unknown> } }
        ).fonts.ready,
    );

    const size = pageSizeFor(params.format);
    const pdfBuffer = await page.pdf({
      ...size,
      printBackground: true,
      margin: { top: "0", right: "0", bottom: "0", left: "0" },
    });

    const baseName =
      params.path.split("/").pop()?.replace(/\.html?$/i, "") ?? "design";
    const body = new Uint8Array(pdfBuffer);
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="${baseName}.pdf"`,
        "Cache-Control": "no-store",
      },
    });
  } finally {
    await browser.close();
  }
}

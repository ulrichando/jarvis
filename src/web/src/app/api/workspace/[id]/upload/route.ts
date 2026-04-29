import { NextResponse, type NextRequest } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { z } from "zod";
import { resolveSafe, touchWorkspace } from "@/lib/workspace/storage";

export const runtime = "nodejs";
export const maxDuration = 60;

// 10MB ceiling per upload. Bigger artifacts (videos, design archives) belong
// in dedicated storage, not the workspace tree.
const MAX_BYTES = 10 * 1024 * 1024;

const UploadSchema = z.object({
  path: z.string().min(1).max(512),
  base64: z.string().min(1),
});

export async function POST(
  req: NextRequest,
  ctx: RouteContext<"/api/workspace/[id]/upload">,
) {
  const { id } = await ctx.params;
  let body;
  try {
    body = UploadSchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json(
      { error: "invalid body", detail: err instanceof Error ? err.message : String(err) },
      { status: 400 },
    );
  }

  // Strip the optional data:URL prefix the browser FileReader hands us so the
  // wire format is just raw base64.
  const m = body.base64.match(/^data:[^;]+;base64,(.+)$/);
  const b64 = m ? m[1] : body.base64;

  let buf: Buffer;
  try {
    buf = Buffer.from(b64, "base64");
  } catch {
    return NextResponse.json({ error: "invalid base64" }, { status: 400 });
  }
  if (buf.length === 0) {
    return NextResponse.json({ error: "empty file" }, { status: 400 });
  }
  if (buf.length > MAX_BYTES) {
    return NextResponse.json(
      { error: `file > ${MAX_BYTES} bytes` },
      { status: 413 },
    );
  }

  let abs: string;
  try {
    abs = resolveSafe(id, body.path);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "invalid path" },
      { status: 400 },
    );
  }

  try {
    await fs.mkdir(path.dirname(abs), { recursive: true });
    await fs.writeFile(abs, buf);
    await touchWorkspace(id);
    return NextResponse.json({ ok: true, path: body.path, size: buf.length });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "write failed" },
      { status: 500 },
    );
  }
}

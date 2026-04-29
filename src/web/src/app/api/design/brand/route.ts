import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { getBrand, putBrand, putBrandAsset } from "@/lib/design/brand";

export const runtime = "nodejs";

const ColorsSchema = z.object({
  bg: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  fg: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  accent: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  muted: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  supporting: z.string().regex(/^#[0-9a-fA-F]{6}$/),
});

const FontSchema = z.object({
  family: z.string().min(1).max(80),
  googleFontsUrl: z.string().url().optional(),
});

const BrandSchema = z.object({
  version: z.literal(1),
  name: z.string().min(1).max(80),
  logoPath: z.string().optional(),
  colors: ColorsSchema,
  fonts: z.object({ display: FontSchema, body: FontSchema }),
  voice: z.string().max(400).optional(),
  references: z
    .array(z.object({ path: z.string(), note: z.string().optional() }))
    .optional(),
});

const PutSchema = z.object({
  brand: BrandSchema,
  logoBase64: z.string().optional(),
  logoFilename: z.string().optional(),
});

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("workspaceId");
  if (!id) {
    return NextResponse.json({ error: "workspaceId required" }, { status: 400 });
  }
  const brand = await getBrand(id);
  return NextResponse.json({ brand });
}

export async function PUT(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("workspaceId");
  if (!id) {
    return NextResponse.json({ error: "workspaceId required" }, { status: 400 });
  }

  let parsed;
  try {
    parsed = PutSchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json(
      { error: "invalid body", detail: err instanceof Error ? err.message : String(err) },
      { status: 400 },
    );
  }

  let next = parsed.brand;

  if (parsed.logoBase64 && parsed.logoFilename) {
    const m = parsed.logoBase64.match(/^data:[^;]+;base64,(.+)$/);
    const b64 = m ? m[1] : parsed.logoBase64;
    const data = Buffer.from(b64, "base64");
    if (data.length > 2 * 1024 * 1024) {
      return NextResponse.json({ error: "logo > 2MB" }, { status: 413 });
    }
    try {
      const stored = await putBrandAsset(id, parsed.logoFilename, data);
      next = { ...next, logoPath: stored };
    } catch (err) {
      return NextResponse.json(
        { error: err instanceof Error ? err.message : "asset write failed" },
        { status: 400 },
      );
    }
  }

  await putBrand(id, next);
  return NextResponse.json({ brand: next });
}

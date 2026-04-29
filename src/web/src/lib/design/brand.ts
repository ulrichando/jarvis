import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveSafe, workspaceRoot } from "@/lib/workspace/storage";

export type BrandColors = {
  bg: string;
  fg: string;
  accent: string;
  muted: string;
  supporting: string;
};

export type BrandFont = {
  family: string;
  googleFontsUrl?: string;
};

export type Brand = {
  version: 1;
  name: string;
  logoPath?: string;
  colors: BrandColors;
  fonts: { display: BrandFont; body: BrandFont };
  voice?: string;
  references?: { path: string; note?: string }[];
};

const BRAND_DIR_REL = ".jarvis/brand";
const BRAND_FILE_REL = ".jarvis/brand.json";

export async function getBrand(workspaceId: string): Promise<Brand | null> {
  const file = resolveSafe(workspaceId, BRAND_FILE_REL);
  try {
    const buf = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(buf);
    if (parsed?.version !== 1) return null;
    return parsed as Brand;
  } catch {
    return null;
  }
}

export async function putBrand(workspaceId: string, brand: Brand): Promise<void> {
  const dir = resolveSafe(workspaceId, ".jarvis");
  await fs.mkdir(dir, { recursive: true });
  const file = resolveSafe(workspaceId, BRAND_FILE_REL);
  await fs.writeFile(file, JSON.stringify(brand, null, 2), "utf8");
}

export async function putBrandAsset(
  workspaceId: string,
  filename: string,
  data: Buffer,
): Promise<string> {
  const safe = path.basename(filename);
  if (safe !== filename || safe.startsWith(".")) {
    throw new Error("invalid asset filename");
  }
  const dir = resolveSafe(workspaceId, BRAND_DIR_REL);
  await fs.mkdir(dir, { recursive: true });
  const dest = path.join(dir, safe);
  // Final sanity: dest must still be inside the workspace.
  const root = workspaceRoot(workspaceId);
  if (!dest.startsWith(root + path.sep)) {
    throw new Error("path escape detected");
  }
  await fs.writeFile(dest, data);
  return path.join(BRAND_DIR_REL, safe);
}

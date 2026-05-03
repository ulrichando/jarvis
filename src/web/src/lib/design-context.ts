import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveSafe, workspaceRoot } from "@/lib/workspace/storage";

// Reads the workspace's `design/` folder (if present) and returns a
// formatted context block for the workbench system prompt. Embedding
// the design upfront — instead of asking the model to `cat` it via
// shell on every turn — eliminates the "diagnose loop" and gives the
// model the actual visual reference to replicate from word one.
//
// Production AI coders all do this: Bolt + Lovable preload Figma
// context, v0 preloads the chat's image attachments. JARVIS gets the
// same treatment via the workspace's `design/` folder, which the
// Design tab already populates.

const TEXT_EXT = new Set([
  "tsx",
  "ts",
  "jsx",
  "js",
  "mjs",
  "html",
  "htm",
  "css",
  "scss",
  "json",
  "md",
  "txt",
  "yml",
  "yaml",
  "svg",
]);

const IGNORE_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  "dist",
  "build",
  ".turbo",
  ".cache",
  ".jarvis",
]);

const TOTAL_BYTES_CAP = 60_000;
const PER_FILE_BYTES_CAP = 12_000;

type DesignFile = {
  path: string;
  bytes: number;
  content: string;
  truncated: boolean;
};

async function walkDesignDir(
  workspaceId: string,
  rel: string,
  out: DesignFile[],
  remaining: { bytes: number },
): Promise<void> {
  if (remaining.bytes <= 0) return;
  const abs = resolveSafe(workspaceId, rel);
  let entries;
  try {
    entries = await fs.readdir(abs, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    if (remaining.bytes <= 0) return;
    if (e.name.startsWith(".") && e.name !== ".env") continue;
    const childRel = path.posix.join(rel, e.name);
    if (e.isDirectory()) {
      if (IGNORE_DIRS.has(e.name)) continue;
      await walkDesignDir(workspaceId, childRel, out, remaining);
    } else if (e.isFile()) {
      const ext = e.name.split(".").pop()?.toLowerCase() ?? "";
      if (!TEXT_EXT.has(ext)) continue;
      const fullPath = path.join(workspaceRoot(workspaceId), childRel);
      try {
        const stat = await fs.stat(fullPath);
        if (stat.size > 200_000) continue; // way too big, skip
        let content = await fs.readFile(fullPath, "utf8");
        let truncated = false;
        if (content.length > PER_FILE_BYTES_CAP) {
          content = content.slice(0, PER_FILE_BYTES_CAP);
          truncated = true;
        }
        const cost = Math.min(content.length, remaining.bytes);
        if (cost <= 0) continue;
        if (content.length > cost) {
          content = content.slice(0, cost);
          truncated = true;
        }
        remaining.bytes -= cost;
        out.push({
          path: childRel,
          bytes: stat.size,
          content,
          truncated,
        });
      } catch {
        /* skip unreadable */
      }
    }
  }
}

/**
 * Returns a system-prompt fragment containing the workspace's design
 * reference files, or an empty string if there is no `design/` folder
 * (or it's empty / all binaries). Capped at ~60KB total.
 */
export async function buildDesignContextBlock(
  workspaceId: string,
): Promise<string> {
  // Bail quickly if `design/` doesn't exist.
  try {
    const stat = await fs.stat(resolveSafe(workspaceId, "design"));
    if (!stat.isDirectory()) return "";
  } catch {
    return "";
  }
  const out: DesignFile[] = [];
  const remaining = { bytes: TOTAL_BYTES_CAP };
  await walkDesignDir(workspaceId, "design", out, remaining);
  if (out.length === 0) return "";
  out.sort((a, b) => a.path.localeCompare(b.path));
  const totalBytes = TOTAL_BYTES_CAP - remaining.bytes;
  const lines: string[] = [];
  lines.push("");
  lines.push(
    "<design_reference>",
  );
  lines.push(
    `  The workspace ships with a design/ folder containing the visual reference for THIS PROJECT. ${out.length} file(s) read (${totalBytes.toLocaleString()} bytes).`,
  );
  lines.push("");
  lines.push(
    "  YOUR JOB IS TO REPLICATE THIS DESIGN. Same copy, same components, same layout, same color tokens. Don't invent fictional content. Don't ship a generic 'Welcome to <project>' template. The runtime will screenshot the live preview after each turn and the user (and you, if multimodal) will compare against this reference.",
  );
  lines.push("");
  for (const f of out) {
    lines.push(
      `  ─── ${f.path}${f.truncated ? " [truncated]" : ""} (${f.bytes} bytes)`,
    );
    lines.push("```");
    lines.push(f.content);
    lines.push("```");
    lines.push("");
  }
  lines.push("</design_reference>");
  return lines.join("\n");
}

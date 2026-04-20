// File ops for misty: read, write, edit, glob, grep.
// Shells out to ripgrep/find when faster than re-implementing in JS.

import type { ToolRunner } from "../types.ts";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

const MAX_OUTPUT = 16_384;
const MAX_READ_BYTES = 200_000;

function truncate(s: string, limit = MAX_OUTPUT): string {
  return s.length > limit ? s.slice(0, limit) + `\n[truncated; original ${s.length} bytes]` : s;
}

export const readFileTool: ToolRunner = {
  def: {
    name: "read_file",
    description: "Read a file from disk. Returns first 200KB; pass offset/limit for more. Use absolute paths.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Absolute path to the file" },
        offset: { type: "number", description: "Byte offset to start reading (default 0)" },
        limit: { type: "number", description: "Max bytes to read (default 200000)" },
      },
      required: ["path"],
    },
  },
  async run(input: unknown) {
    const { path, offset = 0, limit = MAX_READ_BYTES } = input as { path: string; offset?: number; limit?: number };
    if (typeof path !== "string" || !path.startsWith("/")) {
      return { output: "read_file: path must be an absolute path", is_error: true };
    }
    try {
      const buf = await readFile(path);
      const slice = buf.subarray(offset, offset + limit);
      const text = slice.toString("utf8");
      const hdr = `${path} (${buf.length} bytes total, reading ${slice.length} from offset ${offset})\n`;
      return { output: hdr + truncate(text) };
    } catch (err) {
      return { output: `read_file: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

export const writeFileTool: ToolRunner = {
  def: {
    name: "write_file",
    description: "Write text content to a file (overwrites existing). Creates parent directories.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Absolute path to the file" },
        content: { type: "string", description: "Full file content to write" },
      },
      required: ["path", "content"],
    },
  },
  async run(input: unknown) {
    const { path, content } = input as { path: string; content: string };
    if (typeof path !== "string" || !path.startsWith("/")) {
      return { output: "write_file: path must be an absolute path", is_error: true };
    }
    try {
      await mkdir(dirname(path), { recursive: true });
      await writeFile(path, content ?? "", "utf8");
      return { output: `wrote ${Buffer.byteLength(content ?? "", "utf8")} bytes to ${path}` };
    } catch (err) {
      return { output: `write_file: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

export const editFileTool: ToolRunner = {
  def: {
    name: "edit_file",
    description: "Replace an exact string in a file with a new string. Fails if old_string is absent or ambiguous.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Absolute path to the file" },
        old_string: { type: "string", description: "Exact text to find" },
        new_string: { type: "string", description: "Replacement text" },
        replace_all: { type: "boolean", description: "Replace every occurrence (default false)" },
      },
      required: ["path", "old_string", "new_string"],
    },
  },
  async run(input: unknown) {
    const { path, old_string, new_string, replace_all } = input as {
      path: string; old_string: string; new_string: string; replace_all?: boolean;
    };
    if (typeof path !== "string" || !path.startsWith("/")) {
      return { output: "edit_file: path must be an absolute path", is_error: true };
    }
    try {
      const current = await readFile(path, "utf8");
      const count = current.split(old_string).length - 1;
      if (count === 0) return { output: "edit_file: old_string not found in file", is_error: true };
      if (count > 1 && !replace_all) {
        return { output: `edit_file: old_string matches ${count} times; pass replace_all=true or make it unique`, is_error: true };
      }
      const next = replace_all
        ? current.split(old_string).join(new_string)
        : current.replace(old_string, new_string);
      await writeFile(path, next, "utf8");
      return { output: `edited ${path} (${count} replacement${count === 1 ? "" : "s"})` };
    } catch (err) {
      return { output: `edit_file: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

export const globTool: ToolRunner = {
  def: {
    name: "glob",
    description: "Find files matching a glob pattern (e.g. '**/*.ts'). Returns up to 200 paths.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Glob pattern" },
        cwd: { type: "string", description: "Directory to search from (default /)" },
      },
      required: ["pattern"],
    },
  },
  async run(input: unknown) {
    const { pattern, cwd = "/" } = input as { pattern: string; cwd?: string };
    if (typeof pattern !== "string" || pattern.length === 0) {
      return { output: "glob: pattern is required", is_error: true };
    }
    try {
      const g = new Bun.Glob(pattern);
      const hits: string[] = [];
      for await (const f of g.scan({ cwd, absolute: true, onlyFiles: true })) {
        hits.push(f);
        if (hits.length >= 200) break;
      }
      return { output: hits.length === 0 ? "(no matches)" : hits.join("\n") };
    } catch (err) {
      return { output: `glob: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

export const grepTool: ToolRunner = {
  def: {
    name: "grep",
    description: "Search file contents with ripgrep. Returns up to 200 matching lines with file:line prefixes.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Regex pattern" },
        path: { type: "string", description: "Directory or file to search (default cwd)" },
        glob: { type: "string", description: "Optional glob filter (e.g. '*.ts')" },
        case_insensitive: { type: "boolean", description: "Case-insensitive match (default false)" },
      },
      required: ["pattern"],
    },
  },
  async run(input: unknown) {
    const { pattern, path = ".", glob, case_insensitive } = input as {
      pattern: string; path?: string; glob?: string; case_insensitive?: boolean;
    };
    if (typeof pattern !== "string" || pattern.length === 0) {
      return { output: "grep: pattern is required", is_error: true };
    }
    const args = ["rg", "--line-number", "--max-count", "50", "--with-filename"];
    if (case_insensitive) args.push("-i");
    if (glob) args.push("-g", glob);
    args.push("--", pattern, path);
    try {
      const proc = Bun.spawn(args, { stdout: "pipe", stderr: "pipe" });
      const out = await new Response(proc.stdout).text();
      const err = await new Response(proc.stderr).text();
      await proc.exited;
      const combined = out + (err && !out ? err : "");
      if (!combined.trim()) return { output: "(no matches)" };
      const lines = combined.split("\n").slice(0, 200).join("\n");
      return { output: truncate(lines) };
    } catch (err) {
      return { output: `grep: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

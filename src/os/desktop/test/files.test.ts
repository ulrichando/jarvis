import { test, expect } from "bun:test";
import { readFileTool, writeFileTool, editFileTool, globTool, grepTool } from "../agent/tools/files.ts";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function temp() {
  return mkdtemp(join(tmpdir(), "misty-files-test-"));
}

test("write_file + read_file round-trip", async () => {
  const dir = await temp();
  const p = `${dir}/hello.txt`;
  const w = await writeFileTool.run({ path: p, content: "hi misty" });
  expect(w.is_error).toBeFalsy();
  const r = await readFileTool.run({ path: p });
  expect(r.output).toContain("hi misty");
});

test("write_file creates parent directories", async () => {
  const dir = await temp();
  const p = `${dir}/deep/nested/f.txt`;
  const w = await writeFileTool.run({ path: p, content: "ok" });
  expect(w.is_error).toBeFalsy();
});

test("edit_file replaces exact match", async () => {
  const dir = await temp();
  const p = `${dir}/edit.txt`;
  await writeFileTool.run({ path: p, content: "hello world" });
  const e = await editFileTool.run({ path: p, old_string: "world", new_string: "misty" });
  expect(e.is_error).toBeFalsy();
  const r = await readFileTool.run({ path: p });
  expect(r.output).toContain("hello misty");
});

test("edit_file errors when old_string not found", async () => {
  const dir = await temp();
  const p = `${dir}/edit.txt`;
  await writeFileTool.run({ path: p, content: "abc" });
  const e = await editFileTool.run({ path: p, old_string: "xyz", new_string: "123" });
  expect(e.is_error).toBe(true);
});

test("edit_file errors on ambiguous match without replace_all", async () => {
  const dir = await temp();
  const p = `${dir}/edit.txt`;
  await writeFileTool.run({ path: p, content: "foo foo foo" });
  const e = await editFileTool.run({ path: p, old_string: "foo", new_string: "bar" });
  expect(e.is_error).toBe(true);
});

test("edit_file replace_all=true rewrites every match", async () => {
  const dir = await temp();
  const p = `${dir}/edit.txt`;
  await writeFileTool.run({ path: p, content: "foo foo foo" });
  const e = await editFileTool.run({ path: p, old_string: "foo", new_string: "bar", replace_all: true });
  expect(e.is_error).toBeFalsy();
  const r = await readFileTool.run({ path: p });
  expect(r.output).toContain("bar bar bar");
});

test("read_file rejects non-absolute path", async () => {
  const r = await readFileTool.run({ path: "relative/path.txt" });
  expect(r.is_error).toBe(true);
});

test("glob finds files in a directory", async () => {
  const dir = await temp();
  await writeFileTool.run({ path: `${dir}/a.md`, content: "1" });
  await writeFileTool.run({ path: `${dir}/b.md`, content: "2" });
  await writeFileTool.run({ path: `${dir}/c.txt`, content: "3" });
  const g = await globTool.run({ pattern: "*.md", cwd: dir });
  expect(g.is_error).toBeFalsy();
  expect(g.output).toContain("a.md");
  expect(g.output).toContain("b.md");
  expect(g.output).not.toContain("c.txt");
});

test("grep finds matching lines via ripgrep (when rg is installed)", async () => {
  // On hosts without rg on PATH (e.g. claude-code dev shell where rg is a function),
  // skip this test — the VM has ripgrep installed via base-devel.
  const check = Bun.spawnSync(["sh", "-c", "command -v rg"]);
  if (check.exitCode !== 0) return;
  const dir = await temp();
  await writeFileTool.run({ path: `${dir}/file.txt`, content: "alpha\nbeta\ngamma\n" });
  const r = await grepTool.run({ pattern: "beta", path: dir });
  expect(r.is_error).toBeFalsy();
  expect(r.output).toContain("beta");
});

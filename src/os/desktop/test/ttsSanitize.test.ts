import { test, expect } from "bun:test";
import { sanitizeForTTS } from "../voice/tts.ts";

test("strips fenced code blocks", () => {
  const out = sanitizeForTTS("Here is the code:\n```bash\nls -la\n```\nDone.");
  expect(out).not.toContain("```");
  expect(out).not.toContain("ls -la");
  expect(out).toContain("Done");
});

test("unwraps short inline code", () => {
  const out = sanitizeForTTS("The file is `config.ts`.");
  expect(out).not.toContain("`");
  expect(out).toContain("config.ts");
});

test("drops long inline code", () => {
  const long = "x".repeat(100);
  const out = sanitizeForTTS(`Here is \`${long}\` for you`);
  expect(out).not.toContain("x".repeat(100));
});

test("strips markdown emphasis", () => {
  expect(sanitizeForTTS("I am **JARVIS**")).toBe("I am JARVIS");
  expect(sanitizeForTTS("Be *careful*")).toBe("Be careful");
});

test("strips headers and bullet markers", () => {
  const out = sanitizeForTTS("# Title\n- item one\n- item two");
  expect(out).not.toContain("#");
  expect(out).not.toContain("- ");
  expect(out).toContain("item one");
  expect(out).toContain("item two");
});

test("replaces URLs with 'a link'", () => {
  const out = sanitizeForTTS("Visit https://example.com/very/long/path for info.");
  expect(out).toContain("a link");
  expect(out).not.toContain("https://");
});

test("drops bare shell prompts", () => {
  const out = sanitizeForTTS("$ npm test\nAll passing.");
  expect(out).not.toContain("$");
  expect(out).toContain("All passing");
});

test("collapses whitespace", () => {
  const out = sanitizeForTTS("hello    \n\n\n   world");
  expect(out).toBe("hello world");
});

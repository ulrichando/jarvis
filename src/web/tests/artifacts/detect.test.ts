import { describe, expect, test } from "vitest";
import { detectArtifacts } from "@/lib/artifacts/detect";

const reactBlock = [
  "## Counter",
  "```tsx",
  'import { useState } from "react";',
  "export default function Counter() {",
  "  const [n, setN] = useState(0);",
  "  return <button onClick={() => setN(n + 1)}>{n}</button>;",
  "}",
  "```",
].join("\n");

describe("detectArtifacts", () => {
  test("detects a React component (export default + JSX) regardless of size", () => {
    const got = detectArtifacts(reactBlock);
    expect(got).toHaveLength(1);
    expect(got[0].kind).toBe("react");
    expect(got[0].title).toBe("Counter");
    expect(got[0].content).toContain("export default");
  });

  test("detects a full HTML document", () => {
    const html = "```html\n<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n```";
    const got = detectArtifacts(html);
    expect(got[0].kind).toBe("html");
  });

  test("detects svg + mermaid", () => {
    const svg = detectArtifacts("```svg\n<svg viewBox='0 0 1 1'></svg>\n```");
    expect(svg[0].kind).toBe("svg");
    const mmd = detectArtifacts("```mermaid\ngraph TD;\nA-->B;\nC-->D;\n```");
    expect(mmd[0].kind).toBe("mermaid");
  });

  test("ignores a SHORT plain code snippet (under the line threshold)", () => {
    const short = "Here:\n```js\nconst a = 1;\nconsole.log(a);\n```";
    expect(detectArtifacts(short)).toEqual([]);
  });

  test("detects a LONG plain code snippet as a code artifact", () => {
    const long =
      "```python\n" + Array.from({ length: 20 }, (_, i) => `x${i} = ${i}`).join("\n") + "\n```";
    const got = detectArtifacts(long);
    expect(got[0].kind).toBe("code");
    expect(got[0].language).toBe("python");
  });

  test("skips content already wrapped in <jarvisArtifact> (the tag path owns it)", () => {
    const tagged =
      '<jarvisArtifact kind="react" slug="x" title="X">\n```tsx\nexport default () => <div/>;\n```\n</jarvisArtifact>';
    expect(detectArtifacts(tagged)).toEqual([]);
  });

  test("plain prose with no code → []", () => {
    expect(detectArtifacts("Just explaining something, no code here.")).toEqual([]);
  });

  test("detects csv (table) and substantial json", () => {
    const csv = detectArtifacts(
      "```csv\na,b,c\n1,2,3\n4,5,6\n7,8,9\n10,11,12\n```",
    );
    expect(csv[0]?.kind).toBe("csv");
    const json =
      "```json\n{\n" +
      Array.from({ length: 20 }, (_, i) => `  "k${i}": ${i}`).join(",\n") +
      "\n}\n```";
    expect(detectArtifacts(json)[0]?.kind).toBe("json");
  });

  test("distinct same-titled blocks in one message get distinct slugs", () => {
    const two =
      "```html\n<!doctype html><html><body>1</body></html>\n```\n\n" +
      "```html\n<!doctype html><html><body>2</body></html>\n```";
    const got = detectArtifacts(two);
    expect(got).toHaveLength(2);
    expect(new Set(got.map((a) => a.slug)).size).toBe(2);
  });
});

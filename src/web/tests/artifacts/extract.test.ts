import { describe, expect, test } from "vitest";
import { extractJarvisArtifacts } from "@/lib/artifacts/extract";

describe("extractJarvisArtifacts", () => {
  test("pulls a single artifact with metadata + content", () => {
    const text =
      `Sure, here you go.\n` +
      `<jarvisArtifact kind="react" slug="counter" title="Counter" language="tsx">` +
      `export default () => null;` +
      `</jarvisArtifact>`;
    const got = extractJarvisArtifacts(text);
    expect(got).toHaveLength(1);
    expect(got[0]).toMatchObject({
      slug: "counter",
      kind: "react",
      title: "Counter",
      language: "tsx",
      content: "export default () => null;",
    });
  });

  test("pulls multiple artifacts in one message", () => {
    const text =
      `<jarvisArtifact kind="svg" slug="logo" title="Logo"><svg/></jarvisArtifact>` +
      `<jarvisArtifact kind="mermaid" slug="flow" title="Flow">graph TD; A-->B</jarvisArtifact>`;
    const got = extractJarvisArtifacts(text);
    expect(got.map((a) => a.slug)).toEqual(["logo", "flow"]);
    expect(got.map((a) => a.kind)).toEqual(["svg", "mermaid"]);
  });

  test("same slug twice keeps the LAST occurrence", () => {
    const text =
      `<jarvisArtifact kind="code" slug="x" title="v1">A</jarvisArtifact>` +
      `<jarvisArtifact kind="code" slug="x" title="v2">B</jarvisArtifact>`;
    const got = extractJarvisArtifacts(text);
    expect(got).toHaveLength(1);
    expect(got[0].title).toBe("v2");
    expect(got[0].content).toBe("B");
  });

  test("ignores fenced code that merely mentions the tag", () => {
    const text = "Here is plain prose with a ```js\nconst a = 1;\n``` block.";
    expect(extractJarvisArtifacts(text)).toEqual([]);
  });

  test("returns [] for plain prose", () => {
    expect(extractJarvisArtifacts("just a normal answer")).toEqual([]);
  });

  test("strips a stray wrapping code fence + unescapes entities (non-markdown)", () => {
    const text =
      `<jarvisArtifact kind="html" slug="p" title="P">` +
      "```html\n<div>&lt;hi&gt;</div>\n```" +
      `</jarvisArtifact>`;
    const got = extractJarvisArtifacts(text);
    expect(got[0].content).toBe("<div><hi></div>");
  });

  test("missing slug is skipped (no identity to version)", () => {
    const text = `<jarvisArtifact kind="code" title="No slug">x</jarvisArtifact>`;
    expect(extractJarvisArtifacts(text)).toEqual([]);
  });

  test("invalid kind falls back to code", () => {
    const text = `<jarvisArtifact kind="banana" slug="s" title="T">z</jarvisArtifact>`;
    expect(extractJarvisArtifacts(text)[0].kind).toBe("code");
  });
});

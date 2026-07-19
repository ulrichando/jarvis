import { describe, expect, test } from "vitest";
import { sanitizeMermaidSource } from "@/lib/artifacts/mermaid";

const DIAGRAM = "flowchart TD\n  A[Start] --> J[Stop]\n  end";

describe("sanitizeMermaidSource", () => {
  test("clean source passes through (trimmed)", () => {
    expect(sanitizeMermaidSource(`\n${DIAGRAM}\n`)).toBe(DIAGRAM);
  });

  test("strips a leaked </jarvisArtifact> and everything after it", () => {
    // The exact live-bug shape: source ended `…end</jarvisArtifact>-`.
    expect(sanitizeMermaidSource(`${DIAGRAM}</jarvisArtifact>-`)).toBe(DIAGRAM);
    expect(
      sanitizeMermaidSource(`${DIAGRAM}</jarvisArtifact>\n- trailing prose\nmore`),
    ).toBe(DIAGRAM);
  });

  test("case-insensitive tag match", () => {
    expect(sanitizeMermaidSource(`${DIAGRAM}</JARVISARTIFACT>junk`)).toBe(DIAGRAM);
    expect(sanitizeMermaidSource(`${DIAGRAM}</JarvisArtifact>`)).toBe(DIAGRAM);
  });

  test("strips a trailing PARTIAL close tag (streaming boundary)", () => {
    expect(sanitizeMermaidSource(`${DIAGRAM}</jarvisArtif`)).toBe(DIAGRAM);
    expect(sanitizeMermaidSource(`${DIAGRAM}</jarvisartifact`)).toBe(DIAGRAM);
    expect(sanitizeMermaidSource(`${DIAGRAM}</`)).toBe(DIAGRAM);
  });

  test("tag without the closing > still stripped with its trailing text", () => {
    // indexOf matches the `</jarvisartifact` prefix, so text after a
    // malformed tag goes too.
    expect(sanitizeMermaidSource(`${DIAGRAM}</jarvisArtifact -`)).toBe(DIAGRAM);
  });

  test("leaves a lone `<` and unrelated closing tags alone", () => {
    const withLt = `flowchart TD\n  A["x <"] --> B`;
    expect(sanitizeMermaidSource(withLt)).toBe(withLt);
    const withOtherTag = `flowchart TD\n  A["</b>"] --> B`;
    expect(sanitizeMermaidSource(withOtherTag)).toBe(withOtherTag);
  });

  test("empty and tag-only input yield empty string", () => {
    expect(sanitizeMermaidSource("")).toBe("");
    expect(sanitizeMermaidSource("</jarvisArtifact>")).toBe("");
  });
});

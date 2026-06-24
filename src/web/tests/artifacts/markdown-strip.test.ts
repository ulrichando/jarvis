import { describe, expect, test } from "vitest";
import { stripDesignTags } from "@/components/markdown/markdown";

describe("stripDesignTags — jarvisArtifact", () => {
  test("removes a <jarvisArtifact> block from visible prose", () => {
    const input =
      `Here is a counter.\n` +
      `<jarvisArtifact kind="react" slug="counter" title="Counter">` +
      `export default function App(){return <button>0</button>}` +
      `</jarvisArtifact>\n` +
      `Let me know if you want changes.`;
    const out = stripDesignTags(input);
    expect(out).toContain("Here is a counter.");
    expect(out).toContain("Let me know if you want changes.");
    expect(out).not.toContain("export default");
    expect(out).not.toContain("jarvisArtifact");
    expect(out).not.toContain("<button>");
  });

  test("drops a truncated (never-closed) artifact block too", () => {
    const input = `Building it.\n<jarvisArtifact kind="html" slug="p" title="P"><h1>hi`;
    const out = stripDesignTags(input);
    expect(out).toContain("Building it.");
    expect(out).not.toContain("<h1>");
    expect(out).not.toContain("jarvisArtifact");
  });

  test("leaves ordinary prose untouched", () => {
    const input = "Just a normal answer with **bold** and `code`.";
    expect(stripDesignTags(input)).toContain("Just a normal answer");
  });
});

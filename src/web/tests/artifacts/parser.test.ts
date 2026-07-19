import { describe, expect, test } from "vitest";
import {
  StreamingMessageParser,
  type JarvisArtifactCallbackData,
  type ArtifactCallbackData,
  type ActionCallbackData,
} from "@/lib/actions/message-parser";

// Drive the parser with a sequence of CUMULATIVE chunks (it resumes from
// state.position each call and slices absolute indices, so each call gets
// the full text so far) and collect every callback event.
function run(chunks: string[]) {
  const events: Array<[string, unknown]> = [];
  const parser = new StreamingMessageParser({
    onArtifactOpen: (d) => events.push(["artifactOpen", d]),
    onArtifactClose: (d) => events.push(["artifactClose", d]),
    onActionOpen: (d) => events.push(["actionOpen", d]),
    onActionClose: (d) => events.push(["actionClose", d]),
    onJarvisArtifactOpen: (d) => events.push(["jarvisOpen", d]),
    onJarvisArtifactStream: (d) => events.push(["jarvisStream", d]),
    onJarvisArtifactClose: (d) => events.push(["jarvisClose", d]),
  });
  let cumulative = "";
  let visible = "";
  for (const c of chunks) {
    cumulative += c;
    visible += parser.parse("m1", cumulative);
  }
  return { events, visible };
}

const REACT_BODY =
  "export default function App(){const[n,setN]=useState(0);return <button onClick={()=>setN(n+1)}>{n}</button>}";

describe("jarvisArtifact parsing", () => {
  test("complete artifact: open+close fire; content excluded from visible prose", () => {
    const input =
      `Here is a counter.\n` +
      `<jarvisArtifact kind="react" slug="counter" title="Counter" language="tsx">${REACT_BODY}</jarvisArtifact>\n` +
      `Done.`;
    const { events, visible } = run([input]);

    // Visible prose keeps the surrounding text, drops the artifact entirely.
    expect(visible).toContain("Here is a counter.");
    expect(visible).toContain("Done.");
    expect(visible).not.toContain("export default");
    expect(visible).not.toContain("jarvisArtifact");

    const open = events.find((e) => e[0] === "jarvisOpen")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(open).toBeDefined();
    expect(open!.slug).toBe("counter");
    expect(open!.kind).toBe("react");
    expect(open!.title).toBe("Counter");
    expect(open!.language).toBe("tsx");

    const close = events.find((e) => e[0] === "jarvisClose")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(close).toBeDefined();
    expect(close!.complete).toBe(true);
    expect(close!.content).toBe(REACT_BODY);
  });

  test("regression: a <boltArtifact> still parses identically with the new branch present", () => {
    const input =
      `<boltArtifact id="x" title="X">` +
      `<boltAction type="file" filePath="a.txt">hello</boltAction>` +
      `</boltArtifact>`;
    const { events, visible } = run([input]);

    // Bolt content lives in the artifact card, not the visible prose.
    expect(visible.trim()).toBe("");
    const aOpen = events.find((e) => e[0] === "artifactOpen")?.[1] as
      | ArtifactCallbackData
      | undefined;
    expect(aOpen?.title).toBe("X");
    const acClose = events.find((e) => e[0] === "actionClose")?.[1] as
      | ActionCallbackData
      | undefined;
    expect(acClose).toBeDefined();
    expect(acClose!.action.type).toBe("file");
    expect((acClose!.action as { content: string }).content).toContain("hello");
    // No jarvisArtifact callbacks fired for a bolt artifact.
    expect(events.some((e) => String(e[0]).startsWith("jarvis"))).toBe(false);
  });

  test("open tag split across chunks still resolves", () => {
    // Chunks are DELTAS (run() accumulates them); the open tag is split
    // mid-attribute across the first two chunks.
    const { events, visible } = run([
      `intro <jarvisArtifact kind="ht`,
      `ml" slug="page" title="Page">`,
      `<h1>hi</h1>`,
      `</jarvisArtifact> outro`,
    ]);
    expect(visible).toContain("intro");
    expect(visible).toContain("outro");
    expect(visible).not.toContain("<h1>");
    const close = events.find((e) => e[0] === "jarvisClose")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(close?.kind).toBe("html");
    expect(close?.slug).toBe("page");
    expect(close?.content).toBe("<h1>hi</h1>");
  });

  test("invalid kind falls back to 'code'", () => {
    const { events } = run([
      `<jarvisArtifact kind="banana" slug="x" title="X">y</jarvisArtifact>`,
    ]);
    const open = events.find((e) => e[0] === "jarvisOpen")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(open?.kind).toBe("code");
  });
});

describe("close tag split across chunk boundaries never leaks into content", () => {
  // Regression for the live Mermaid bug: a chunk ending mid-close-tag
  // (`…end</jarvisArtif`) was consumed into artifact content and the
  // parser position advanced past the tag's start, so indexOf() never
  // found the completed tag — the artifact wedged open and the Mermaid
  // source ended with `end</jarvisArtifact>-` → parse error.
  const MERMAID_BODY = "flowchart TD\n  A[Start] --> J[Stop]\n  end";
  const OPEN = `<jarvisArtifact kind="mermaid" slug="diag" title="Diagram">`;

  function assertNoLeak(chunks: string[]) {
    const { events, visible } = run(chunks);

    // Every streamed snapshot is a clean prefix of the body — no byte of
    // the close tag (full or partial) ever appears in artifact content.
    for (const [name, data] of events) {
      if (name !== "jarvisStream" && name !== "jarvisClose") continue;
      const content = (data as JarvisArtifactCallbackData).content;
      expect(content).not.toMatch(/<\/jarvis/i);
      expect(content).not.toMatch(/<\/$/);
      expect(MERMAID_BODY.startsWith(content)).toBe(true);
    }

    // The artifact actually closes, with exactly the diagram source.
    const close = events.find((e) => e[0] === "jarvisClose")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(close).toBeDefined();
    expect(close!.complete).toBe(true);
    expect(close!.content).toBe(MERMAID_BODY);

    // Trailing prose lands back in the visible output, not the diagram.
    expect(visible).toContain("- after");
    expect(visible).not.toMatch(/<\/jarvis/i);
  }

  test("close tag split mid-tag: `</jarvisArtif` | `act>`", () => {
    assertNoLeak([
      `${OPEN}${MERMAID_BODY}</jarvisArtif`,
      `act>\n- after`,
    ]);
  });

  test("split at the tag boundary: `…end` | `</jarvisArtifact>`", () => {
    assertNoLeak([
      `${OPEN}${MERMAID_BODY}`,
      `</jarvisArtifact>\n- after`,
    ]);
  });

  test("close tag split with only `>` missing: `</jarvisArtifact` | `>`", () => {
    assertNoLeak([
      `${OPEN}${MERMAID_BODY}</jarvisArtifact`,
      `>\n- after`,
    ]);
  });

  test("whole message streamed one character at a time", () => {
    const full = `${OPEN}${MERMAID_BODY}</jarvisArtifact>\n- after`;
    assertNoLeak(full.split(""));
  });

  test("mixed-case split close tag (`</JarvisArtif` | `ACT>`) still resolves", () => {
    assertNoLeak([
      `${OPEN}${MERMAID_BODY}</JarvisArtif`,
      `ACT>\n- after`,
    ]);
  });

  test("a real `</` inside content that is NOT the close tag is preserved", () => {
    // `</x>` is held back for one chunk (could be a tag prefix) but must
    // be released into content once it provably isn't the close tag.
    const body = `flowchart TD\n  A["a </x> b"] --> B`;
    const { events } = run([
      `<jarvisArtifact kind="mermaid" slug="d" title="D">${body.slice(0, body.indexOf("</") + 2)}`,
      `${body.slice(body.indexOf("</") + 2)}</jarvisArtifact>`,
    ]);
    const close = events.find((e) => e[0] === "jarvisClose")?.[1] as
      | JarvisArtifactCallbackData
      | undefined;
    expect(close?.content).toBe(body);
  });
});

describe("jarvisPlan close tag split across chunks (same bug class)", () => {
  test("`</jarvisPl` | `an>` still closes the plan; content clean", () => {
    const plans: Array<{ content: string; complete: boolean }> = [];
    const parser = new StreamingMessageParser({
      onPlan: (d) => plans.push({ content: d.content, complete: d.complete }),
    });
    let cumulative = "";
    let visible = "";
    for (const c of [`<jarvisPlan>step one</jarvisPl`, `an> after`]) {
      cumulative += c;
      visible += parser.parse("m1", cumulative);
    }
    const done = plans.find((p) => p.complete);
    expect(done).toBeDefined();
    expect(done!.content).toBe("step one");
    for (const p of plans) expect(p.content).not.toMatch(/<\/jarvis/i);
    expect(visible).toContain("after");
  });
});

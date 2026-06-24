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

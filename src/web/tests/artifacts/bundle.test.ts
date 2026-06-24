// @vitest-environment node
// esbuild requires a real node environment (jsdom's TextEncoder breaks its
// startup invariant), so this suite opts out of the default jsdom env.
import { describe, expect, test } from "vitest";
import { bundleReactSource } from "@/lib/artifacts/bundle";

describe("bundleReactSource", () => {
  test("bundles a default-export component + injects the mount", async () => {
    const src =
      'import { useState } from "react";\n' +
      "export default function App(){ const [n,setN]=useState(0); return <button onClick={()=>setN(n+1)}>{n}</button>; }";
    const out = await bundleReactSource(src);
    expect("js" in out).toBe(true);
    if ("js" in out) {
      // The mount snippet is present and React stays external (esm.sh import).
      expect(out.js).toContain("createRoot");
      expect(out.js).toMatch(/react-dom\/client|esm\.sh/);
      // JSX got transpiled (no raw <button> tag survives as JSX).
      expect(out.js).not.toContain("</button>");
    }
  });

  test("arrow-function default export works", async () => {
    const out = await bundleReactSource(
      'export default () => <div className="x">ok</div>;',
    );
    expect("js" in out).toBe(true);
  });

  test("rewrites non-react bare imports to esm.sh (any npm lib works)", async () => {
    const out = await bundleReactSource(
      'import { Play } from "lucide-react";\nexport default () => <Play />;',
    );
    expect("js" in out).toBe(true);
    if ("js" in out) {
      expect(out.js).toContain("esm.sh/lucide-react");
      // react itself stays bare → resolved by the iframe import map.
      expect(out.js).not.toContain("esm.sh/react?");
    }
  });

  test("missing default export → friendly error, no throw", async () => {
    const out = await bundleReactSource("function App(){ return null }");
    expect("error" in out).toBe(true);
    if ("error" in out) expect(out.error).toMatch(/export default/i);
  });

  test("a syntax error surfaces as an error, not a throw", async () => {
    const out = await bundleReactSource(
      "export default function App(){ return <div> }",
    );
    expect("error" in out).toBe(true);
  });
});

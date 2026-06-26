import "server-only";
import { parse, serialize } from "parse5";
import type {
  DefaultTreeAdapterMap,
  Token,
} from "parse5";

// HTML→React-component codegen for the /design Build flow.
//
// Architecture: instead of asking the model to TRANSLATE the design
// HTML into React components (which causes drift — every model run
// produces "similar but different" output), we mechanically split
// the HTML server-side into proper component files. The HTML stays
// byte-identical to the original; only the wrapping structure is
// generated.
//
// Output: a complete Next.js 15 App Router scaffold with proper
// file distribution (app/, components/, lib/) where the FRONTEND is
// already 100% finished before the model has even seen the seed.
// The model's job becomes: write the backend (API routes + DB +
// form-handler hydration). Drift is impossible because the model
// never touches the design.

type Element = DefaultTreeAdapterMap["element"];
type Document = DefaultTreeAdapterMap["document"];
type Node = DefaultTreeAdapterMap["node"];
type ParentNode = DefaultTreeAdapterMap["parentNode"];

// Section tags we treat as "top-level structural elements" — each
// becomes its own component file.
const STRUCT_TAGS = new Set([
  "section",
  "header",
  "main",
  "footer",
  "nav",
  "article",
  "aside",
]);

export type CodegenResult = {
  /** Files to write into the workspace root. Path is relative,
   *  content is the file body. */
  files: { path: string; content: string }[];
  /** Names of generated section components, in the order they should
   *  render in app/page.tsx. Useful for the seed-prompt summary. */
  sections: string[];
  /** Names + selectors of forms found in the design — passed to the
   *  model so it knows which API routes to scaffold. */
  forms: { selector: string; intent: string }[];
  /** Whether the design implies an auth feature (login/signup/password
   *  inputs / links to auth routes). When true, Auth.js scaffolding
   *  is auto-included in the build. */
  hasAuth: boolean;
  /** Whether the design has file-upload inputs. When true, a basic
   *  upload API route + local-disk storage is scaffolded. Production
   *  swap to Vercel Blob / S3 is a one-line change documented in
   *  app/api/upload/route.ts. */
  hasUploads: boolean;
};

// ── Public API ────────────────────────────────────────────────────────

export function generateScaffold(args: {
  html: string;
  workspaceName: string;
  brand?: {
    name: string;
    colors: { bg: string; fg: string; accent: string };
    fonts: { display: { family: string }; body: { family: string } };
  };
}): CodegenResult {
  const doc = parse(args.html);
  const head = findElement(doc, "head");
  const body = findElement(doc, "body");

  // Extract head content for layout.tsx + globals.css.
  const fontLinks = head ? extractGoogleFontLinks(head) : [];
  const styleBlocks = head ? extractStyleBlocks(head) : [];
  const title =
    (head ? getTextOf(findElement(head, "title")) : "") ||
    args.workspaceName;
  const description =
    (head ? getMetaContent(head, "description") : "") || "";

  // Body sections — auto strategy.
  const sections = body ? splitSectionsAuto(body) : [];

  // Detect forms upfront — needed to template lib/db.ts +
  // app/api/<intent>/route.ts + components/FormsHydrate.tsx.
  const forms = body ? detectForms(body) : [];
  // Industry-aligned auto-detection (Lovable/Bolt/Replit pattern):
  // when the design implies auth or uploads, scaffold those backends
  // automatically. Boundary follows the research synthesis — auto-on
  // for "stateful primitives the app cannot run without" (DB, auth,
  // uploads), opt-in for outbound integrations (email, payments).
  const hasAuth = body ? detectAuthIntent(body) : false;
  const hasUploads = body ? detectUploadIntent(body) : false;

  // Generate files.
  const files: { path: string; content: string }[] = [];

  // 1. package.json — extra deps if auth/uploads/postgres detected
  files.push({
    path: "package.json",
    content: pkgJson(args.workspaceName, { hasAuth, hasUploads }),
  });

  // 2. tsconfig.json
  files.push({
    path: "tsconfig.json",
    content: tsconfigJson(),
  });

  // 3. next.config.js
  files.push({
    path: "next.config.js",
    content: nextConfigJs(),
  });

  // 4. postcss.config.js
  files.push({
    path: "postcss.config.js",
    content: postcssConfigJs(),
  });

  // 5. tailwind.config.ts
  files.push({
    path: "tailwind.config.ts",
    content: tailwindConfigTs(),
  });

  // 6. app/globals.css — copy any <style> blocks from the design HEAD
  //    so CSS variables, custom fonts, animations all carry over.
  files.push({
    path: "app/globals.css",
    content: globalsCss(styleBlocks),
  });

  // 7. app/layout.tsx — Google Fonts, title, description, metadata
  files.push({
    path: "app/layout.tsx",
    content: layoutTsx({ title, description, fontLinks }),
  });

  // 8. app/page.tsx — composes section components in body order
  files.push({
    path: "app/page.tsx",
    content: pageTsx(sections.map((s) => s.componentName)),
  });

  // 9. components/<Name>.tsx for each section
  for (const s of sections) {
    files.push({
      path: `components/${s.componentName}.tsx`,
      content: sectionComponentTsx(s.componentName, s.html),
    });
  }

  // 9.5. components/FormsHydrate.tsx — REAL hydration, generated
  //      from the detected forms. No stub, no model turn needed.
  //      querySelectors each form, attaches onSubmit that POSTs
  //      FormData → JSON, shows inline success/error UI, prevents
  //      navigation. Refinements (custom validation, multi-step
  //      flows) are still the model's job in chat.
  files.push({
    path: "components/FormsHydrate.tsx",
    content: formsHydrateTsx(forms),
  });

  // 9.6. lib/db.ts — better-sqlite3 init with one table per form.
  //      Idempotent; called by every API route on first hit.
  files.push({
    path: "lib/db.ts",
    content: dbTs(forms),
  });

  // 9.7. app/api/<intent>/route.ts — one POST handler per detected form.
  //      Standard pattern: zod schema → INSERT → 201/400/500.
  for (const f of forms) {
    files.push({
      path: `app/api/${slugify(f.intent)}/route.ts`,
      content: apiRouteTs(f.intent),
    });
  }

  // 9.8. Auth scaffolding (Auth.js v5 / next-auth beta) — only when
  //      auth was detected in the design. Pre-wires credentials +
  //      magic-link providers, with empty OAuth slots the user can
  //      activate by adding env vars in Settings → Secrets.
  if (hasAuth) {
    files.push({ path: "auth.ts", content: authTs() });
    files.push({
      path: "app/api/auth/[...nextauth]/route.ts",
      content: authHandlerTs(),
    });
    files.push({ path: "middleware.ts", content: authMiddlewareTs() });
  }

  // 9.9. Upload route — only when <input type="file"> was detected.
  //      Local-disk storage in dev (uploads/ directory); production
  //      swap to Vercel Blob is a one-line change documented inline.
  if (hasUploads) {
    files.push({
      path: "app/api/upload/route.ts",
      content: uploadRouteTs(),
    });
  }

  // 10. .gitignore (so the AI doesn't commit node_modules etc.)
  files.push({
    path: ".gitignore",
    content: gitignoreTxt(),
  });

  // (forms detected upfront — see top of function)

  return {
    files,
    sections: sections.map((s) => s.componentName),
    forms,
    hasAuth,
    hasUploads,
  };
}

// ── Auth + upload detection ──────────────────────────────────────────

/** True if the design has any UI implying user authentication —
 *  password input, email+password form, link to /login or /signup,
 *  buttons with "sign in" / "log in" / "sign up" / "create account"
 *  text. Conservative: a single signal is enough since the cost of
 *  false-positives (extra Auth.js scaffolding the user ignores) is
 *  much lower than false-negatives (no auth on a login page). */
function detectAuthIntent(body: ParentNode): boolean {
  const AUTH_LINK_RE = /\/(?:login|signin|sign-in|signup|sign-up|register|account|auth)\b/i;
  const AUTH_TEXT_RE =
    /\b(?:sign\s*in|log\s*in|sign\s*up|log\s*out|create\s+account|forgot\s+password|reset\s+password)\b/i;
  let found = false;
  walkElements(body, (el) => {
    if (found) return;
    if (el.tagName === "input") {
      const type = (getAttr(el, "type") ?? "").toLowerCase();
      if (type === "password") {
        found = true;
        return;
      }
    }
    if (el.tagName === "a") {
      const href = getAttr(el, "href") ?? "";
      if (AUTH_LINK_RE.test(href)) {
        found = true;
        return;
      }
    }
    if (el.tagName === "a" || el.tagName === "button") {
      const txt = getTextOf(el);
      if (AUTH_TEXT_RE.test(txt)) {
        found = true;
        return;
      }
    }
  });
  return found;
}

/** True if the design has a file-upload input. Single check: any
 *  `<input type="file">` anywhere in the body. */
function detectUploadIntent(body: ParentNode): boolean {
  let found = false;
  walkElements(body, (el) => {
    if (found || el.tagName !== "input") return;
    const type = (getAttr(el, "type") ?? "").toLowerCase();
    if (type === "file") found = true;
  });
  return found;
}

// ── Section splitting (auto strategy) ─────────────────────────────────

type Section = { componentName: string; html: string };

function splitSectionsAuto(body: Element): Section[] {
  // Strict: only direct children matching STRUCT_TAGS.
  const strict = topLevelStructuralChildren(body);
  if (strict.length >= 3) {
    return finalizeSections(strict);
  }
  // Loose: include direct-child <div>s with an id.
  const loose = topLevelStructuralOrIdDivs(body);
  if (loose.length >= 3) {
    return finalizeSections(loose);
  }
  // Fallback: treat the whole body as a single MainContent component.
  return [
    {
      componentName: "MainContent",
      html: serialize(body),
    },
  ];
}

function topLevelStructuralChildren(parent: ParentNode): Element[] {
  const out: Element[] = [];
  for (const child of getChildren(parent)) {
    if (isElement(child) && STRUCT_TAGS.has(child.tagName)) {
      out.push(child);
    }
  }
  return out;
}

function topLevelStructuralOrIdDivs(parent: ParentNode): Element[] {
  const out: Element[] = [];
  for (const child of getChildren(parent)) {
    if (!isElement(child)) continue;
    if (STRUCT_TAGS.has(child.tagName)) {
      out.push(child);
      continue;
    }
    if (child.tagName === "div" && getAttr(child, "id")) {
      out.push(child);
    }
  }
  return out;
}

function finalizeSections(elements: Element[]): Section[] {
  const used = new Set<string>();
  return elements.map((el, i) => {
    const baseName =
      pascalCase(getAttr(el, "id") || "") ||
      pascalCase(getAttr(el, "data-name") || "") ||
      pascalCase(firstHeadingText(el)) ||
      pascalCase(el.tagName) + (i === 0 ? "" : String(i + 1));
    let name = baseName || `Section${i + 1}`;
    let suffix = 1;
    while (used.has(name)) {
      suffix++;
      name = `${baseName}${suffix}`;
    }
    used.add(name);
    return {
      componentName: name,
      // Use OUTER HTML — `serialize` alone returns inner HTML
      // (children only), which loses the wrapping element's tag,
      // class, id, and any other attributes that styled it. Without
      // outer HTML the `<header class="bg-stone-950 px-8 py-4">`
      // collapses to just its children, and the design's section-
      // level classes evaporate.
      html: outerHtml(el),
    };
  });
}

// parse5 v7 doesn't expose outerHTML directly — `serialize(el)`
// gives ONLY the children's serialized form. Reconstruct: open
// tag with attributes, then children, then close tag. Void
// elements (img/br/hr/input/etc.) self-close. This matters for
// preserving the design's section-level styling.
const VOID_ELEMENTS = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "source", "track", "wbr",
]);

function outerHtml(el: Element): string {
  const tag = el.tagName;
  const attrs = (el.attrs as Token.Attribute[] | undefined) ?? [];
  const attrStr = attrs
    .map((a) => {
      // Preserve namespace prefix if present (e.g. xmlns:xlink)
      const name = a.prefix ? `${a.prefix}:${a.name}` : a.name;
      // Escape quotes inside values.
      const value = a.value.replace(/"/g, "&quot;");
      return ` ${name}="${value}"`;
    })
    .join("");
  if (VOID_ELEMENTS.has(tag)) {
    return `<${tag}${attrStr} />`;
  }
  return `<${tag}${attrStr}>${serialize(el)}</${tag}>`;
}

// ── Form detection ────────────────────────────────────────────────────

function detectForms(
  body: ParentNode,
): { selector: string; intent: string }[] {
  const forms: { selector: string; intent: string }[] = [];
  walkElements(body, (el) => {
    if (el.tagName !== "form") return;
    const id = getAttr(el, "id");
    const dataIntent = getAttr(el, "data-intent");
    const action = getAttr(el, "action");
    const ariaLabel = getAttr(el, "aria-label");
    // Selector preference: id > data-intent > action > tag+heading
    let selector = "";
    let intent = "";
    if (id) {
      selector = `#${id}`;
      intent = dataIntent || ariaLabel || id;
    } else if (action) {
      selector = `form[action="${action}"]`;
      intent = action.replace(/^\//, "").replace(/\//g, "-") || "form";
    } else {
      const heading = firstHeadingText(el);
      selector = "form";
      intent = pascalCase(heading) || "form";
    }
    forms.push({ selector, intent });
  });
  return forms;
}

// ── Head extraction ───────────────────────────────────────────────────

function extractGoogleFontLinks(head: ParentNode): string[] {
  const out: string[] = [];
  walkElements(head, (el) => {
    if (el.tagName !== "link") return;
    const rel = getAttr(el, "rel") || "";
    const href = getAttr(el, "href") || "";
    if (rel === "stylesheet" && /fonts\.googleapis\.com/.test(href)) {
      out.push(href);
    }
    if (rel === "preconnect" && /fonts\.(googleapis|gstatic)\.com/.test(href)) {
      out.push(href);
    }
  });
  return out;
}

function extractStyleBlocks(head: ParentNode): string[] {
  const out: string[] = [];
  walkElements(head, (el) => {
    if (el.tagName !== "style") return;
    out.push(getTextOf(el));
  });
  return out;
}

function getMetaContent(head: ParentNode, name: string): string {
  let result = "";
  walkElements(head, (el) => {
    if (el.tagName !== "meta") return;
    if ((getAttr(el, "name") ?? "").toLowerCase() !== name) return;
    result = getAttr(el, "content") || "";
  });
  return result;
}

// ── parse5 helpers ────────────────────────────────────────────────────

function isElement(node: Node): node is Element {
  return (node as Element).nodeName !== "#text" &&
    (node as Element).nodeName !== "#comment" &&
    "tagName" in node;
}

function getChildren(node: ParentNode): Node[] {
  return (node as { childNodes?: Node[] }).childNodes ?? [];
}

function findElement(root: ParentNode, tagName: string): Element | null {
  for (const child of getChildren(root)) {
    if (isElement(child)) {
      if (child.tagName === tagName) return child;
      const sub = findElement(child, tagName);
      if (sub) return sub;
    }
  }
  return null;
}

function walkElements(
  root: ParentNode,
  visit: (el: Element) => void,
): void {
  for (const child of getChildren(root)) {
    if (isElement(child)) {
      visit(child);
      walkElements(child, visit);
    }
  }
}

function getAttr(el: Element, name: string): string | null {
  const attr = (el.attrs as Token.Attribute[] | undefined)?.find(
    (a) => a.name === name,
  );
  return attr ? attr.value : null;
}

function getTextOf(el: Element | null): string {
  if (!el) return "";
  let out = "";
  for (const child of getChildren(el)) {
    if ((child as { nodeName?: string }).nodeName === "#text") {
      out += (child as { value?: string }).value ?? "";
    } else if (isElement(child)) {
      out += getTextOf(child);
    }
  }
  return out.trim();
}

function firstHeadingText(el: Element): string {
  let result = "";
  walkElements(el, (sub) => {
    if (result) return;
    if (/^h[1-6]$/.test(sub.tagName)) {
      result = getTextOf(sub);
    }
  });
  return result;
}

// ── Naming ────────────────────────────────────────────────────────────

function pascalCase(input: string): string {
  return input
    .replace(/[^a-zA-Z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join("")
    .replace(/^[0-9]+/, ""); // can't start with digit
}

// ── Templates ─────────────────────────────────────────────────────────

function pkgJson(
  name: string,
  opts: { hasAuth: boolean; hasUploads: boolean },
): string {
  // Always include sqlite + pg drivers so lib/db.ts can switch on
  // DATABASE_URL at runtime: sqlite for local dev, postgres for
  // production (Neon / Vercel Postgres / any standard postgres URL).
  // Two driver deps is cheap (~3MB combined); the dual-mode lib/db.ts
  // is what matches the industry pattern (Lovable/Bolt cloud-postgres
  // by default, with a local-dev fallback so the preview works
  // without external infra).
  const dependencies: Record<string, string> = {
    next: "^15.0.0",
    react: "^19.0.0",
    "react-dom": "^19.0.0",
    "better-sqlite3": "^11.0.0",
    pg: "^8.13.0",
    zod: "^3.23.0",
  };
  const devDependencies: Record<string, string> = {
    typescript: "^5.5.0",
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@types/better-sqlite3": "^7.6.0",
    "@types/pg": "^8.11.0",
    tailwindcss: "^4.0.0",
    "@tailwindcss/postcss": "^4.0.0",
    postcss: "^8.4.0",
  };
  if (opts.hasAuth) {
    dependencies["next-auth"] = "5.0.0-beta.25";
    dependencies["@auth/core"] = "^0.37.0";
  }
  return JSON.stringify(
    {
      name: name.toLowerCase().replace(/[^a-z0-9-]/g, "-") || "design-build",
      version: "0.1.0",
      private: true,
      scripts: {
        dev: "next dev -p 5173 -H 0.0.0.0",
        build: "next build",
        start: "next start -p 5173 -H 0.0.0.0",
        lint: "next lint",
      },
      dependencies,
      devDependencies,
    },
    null,
    2,
  );
}

function tsconfigJson(): string {
  return JSON.stringify(
    {
      compilerOptions: {
        target: "ES2022",
        lib: ["dom", "dom.iterable", "esnext"],
        allowJs: true,
        skipLibCheck: true,
        strict: true,
        noEmit: true,
        esModuleInterop: true,
        module: "esnext",
        moduleResolution: "bundler",
        resolveJsonModule: true,
        isolatedModules: true,
        jsx: "preserve",
        incremental: true,
        plugins: [{ name: "next" }],
        paths: { "@/*": ["./*"] },
      },
      include: ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
      exclude: ["node_modules"],
    },
    null,
    2,
  );
}

function nextConfigJs(): string {
  return `/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow Unsplash hot-links + other external image hosts. The design
  // tab uses these heavily; without remotePatterns Next/Image refuses
  // to load them.
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "images.unsplash.com" },
      { protocol: "https", hostname: "**.unsplash.com" },
    ],
  },
};
module.exports = nextConfig;
`;
}

function postcssConfigJs(): string {
  return `module.exports = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
`;
}

function tailwindConfigTs(): string {
  return `import type { Config } from "tailwindcss";
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx,js,jsx}",
    "./components/**/*.{ts,tsx,js,jsx}",
  ],
  theme: { extend: {} },
};
export default config;
`;
}

function globalsCss(styleBlocks: string[]): string {
  return [
    `@import "tailwindcss";`,
    "",
    "/* Carried over from design/landing.html — preserves CSS",
    "   variables, custom font-faces, keyframes, and any other",
    "   global styles the design defined. */",
    ...styleBlocks,
    "",
    `/* Body baseline. The design's <style> rules above set the rest. */`,
    `html, body { margin: 0; padding: 0; }`,
    `body { font-family: var(--font-body, system-ui), sans-serif; }`,
  ].join("\n");
}

function layoutTsx(args: {
  title: string;
  description: string;
  fontLinks: string[];
}): string {
  // JSON.stringify is a complete JS-string-literal encoder (escapes backslash,
  // quote, control chars) and emits the surrounding quotes. Escaping only `"`
  // left a backslash-injection: a title ending in `\` broke out of the string.
  const titleLit = JSON.stringify(args.title);
  const descLit = JSON.stringify(args.description);
  const linkTags = args.fontLinks
    .map((href) => {
      const rel = href.includes("preconnect") ? "preconnect" : "stylesheet";
      return `        <link rel="${rel}" href="${href}" />`;
    })
    .join("\n");
  return `import "./globals.css";
import type { Metadata } from "next";
import FormsHydrate from "@/components/FormsHydrate";

export const metadata: Metadata = {
  title: ${titleLit},
  description: ${descLit},
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
${linkTags || "        {/* Google Fonts links carried over from the design */}"}
      </head>
      <body>
        {children}
        <FormsHydrate />
      </body>
    </html>
  );
}
`;
}

function pageTsx(componentNames: string[]): string {
  if (componentNames.length === 0) {
    return `export default function Page() {
  return (
    <main className="p-8">
      <h1>Empty design — no sections detected.</h1>
    </main>
  );
}
`;
  }
  const imports = componentNames
    .map((n) => `import ${n} from "@/components/${n}";`)
    .join("\n");
  const renders = componentNames.map((n) => `      <${n} />`).join("\n");
  return `${imports}

export default function Page() {
  return (
    <>
${renders}
    </>
  );
}
`;
}

function sectionComponentTsx(name: string, html: string): string {
  // Embed the HTML as a string literal. Use String.raw with a unique
  // tag so backticks and ${} inside the HTML can't break the template.
  // Escape backticks and ${} explicitly.
  const escaped = html
    .replace(/\\/g, "\\\\")
    .replace(/`/g, "\\`")
    .replace(/\$\{/g, "\\${");
  return `// Auto-generated from the /design output. The HTML below is
// byte-identical to the corresponding section of design/landing.html
// — do NOT hand-edit unless you also update the design source.
const HTML = \`${escaped}\`;

export default function ${name}() {
  return <div dangerouslySetInnerHTML={{ __html: HTML }} />;
}
`;
}

function formsHydrateTsx(
  forms: { selector: string; intent: string }[],
): string {
  if (forms.length === 0) {
    return `"use client";
// No forms detected in the design — this is a no-op.
export default function FormsHydrate() {
  return null;
}
`;
  }
  const formsArray = forms
    .map(
      (f) =>
        `  { selector: ${JSON.stringify(f.selector)}, intent: ${JSON.stringify(slugify(f.intent))} },`,
    )
    .join("\n");
  return `"use client";

// Auto-generated from detected forms. Hydrates each <form> in the
// static design HTML with a real onSubmit that POSTs to the API,
// shows inline success/error UI, prevents default navigation.
import { useEffect } from "react";

const FORMS = [
${formsArray}
];

export default function FormsHydrate() {
  useEffect(() => {
    const cleanups: Array<() => void> = [];
    for (const cfg of FORMS) {
      const el = document.querySelector(cfg.selector);
      if (!(el instanceof HTMLFormElement)) continue;
      const handler = async (e: Event) => {
        e.preventDefault();
        const data: Record<string, string> = {};
        for (const [k, v] of new FormData(el).entries()) {
          if (typeof v === "string") data[k] = v;
        }
        // Inline status node — created/reused as a sibling of the form.
        let status = el.querySelector<HTMLDivElement>("[data-form-status]");
        if (!status) {
          status = document.createElement("div");
          status.setAttribute("data-form-status", "");
          status.style.marginTop = "0.75rem";
          status.style.fontSize = "0.875rem";
          el.appendChild(status);
        }
        status.textContent = "Sending…";
        status.style.color = "currentColor";
        try {
          const r = await fetch("/api/" + cfg.intent, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            status.textContent =
              j.message || j.error || "Something went wrong. Try again.";
            status.style.color = "tomato";
            return;
          }
          status.textContent = "Thanks — we'll be in touch.";
          status.style.color = "lightgreen";
          el.reset();
        } catch (err) {
          status.textContent = "Network error. Try again.";
          status.style.color = "tomato";
        }
      };
      el.addEventListener("submit", handler);
      cleanups.push(() => el.removeEventListener("submit", handler));
    }
    return () => {
      for (const c of cleanups) c();
    };
  }, []);
  return null;
}
`;
}

function dbTs(forms: { selector: string; intent: string }[]): string {
  // Industry-aligned dual-mode DB: Postgres in production (DATABASE_URL
  // set — Neon, Vercel Postgres, Supabase, any standard postgres url),
  // sqlite for local dev (no DATABASE_URL). Both drivers are in
  // package.json. Each form gets a `submissions_<intent>` table with
  // a generic JSON payload column — flexible default that the AI can
  // refactor to proper per-form schemas via chat.
  const intents = forms.map((f) => slugify(f.intent).replace(/-/g, "_"));
  const sqliteTables = intents
    .map(
      (n) => `    CREATE TABLE IF NOT EXISTS submissions_${n} (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      payload TEXT NOT NULL,
      created_at INTEGER NOT NULL DEFAULT (unixepoch())
    );`,
    )
    .join("\n");
  const pgTables = intents
    .map(
      (n) => `    CREATE TABLE IF NOT EXISTS submissions_${n} (
      id SERIAL PRIMARY KEY,
      payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );`,
    )
    .join("\n");
  return `// Auto-generated by /api/design/build.
//
// Dual-mode database: Postgres if DATABASE_URL is set (production —
// Neon / Vercel Postgres / Supabase / any pg URL), SQLite otherwise
// (local dev — file at data/app.db). API routes call \`db.insert(...)\`
// without caring which backend is active.
//
// Industry pattern (Lovable, Bolt, Replit): managed Postgres in
// production. We default to that the moment a DATABASE_URL is
// present, and fall back to SQLite so previews work without infra.

import path from "node:path";
import fs from "node:fs";

type Backend =
  | { kind: "sqlite"; db: import("better-sqlite3").Database }
  | { kind: "pg"; pool: import("pg").Pool };

let cached: Backend | null = null;

async function init(): Promise<Backend> {
  if (cached) return cached;
  const url = process.env.DATABASE_URL;
  if (url) {
    const { Pool } = await import("pg");
    const pool = new Pool({
      connectionString: url,
      // Most managed Postgres (Neon, Supabase) want SSL.
      ssl: process.env.PGSSL === "off" ? false : { rejectUnauthorized: false },
    });
    await pool.query(\`
${pgTables}
    \`);
    cached = { kind: "pg", pool };
  } else {
    const Database = (await import("better-sqlite3")).default;
    const dbPath = path.join(process.cwd(), "data", "app.db");
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    const db = new Database(dbPath);
    db.pragma("journal_mode = WAL");
    db.exec(\`
${sqliteTables}
    \`);
    cached = { kind: "sqlite", db };
  }
  return cached;
}

export const db = {
  /** Insert a JSON payload into submissions_<intent>. Resolves to
   *  the new row id. Schema is created lazily on first call. */
  async insert(intent: string, payload: unknown): Promise<number> {
    const backend = await init();
    const table = "submissions_" + intent.replace(/[^a-z0-9_]/gi, "_");
    if (backend.kind === "pg") {
      const r = await backend.pool.query(
        \`INSERT INTO \${table} (payload) VALUES ($1) RETURNING id\`,
        [JSON.stringify(payload)],
      );
      return r.rows[0].id;
    }
    const stmt = backend.db.prepare(
      \`INSERT INTO \${table} (payload) VALUES (?)\`,
    );
    const r = stmt.run(JSON.stringify(payload));
    return Number(r.lastInsertRowid);
  },
};
`;
}

function apiRouteTs(intent: string): string {
  const intentSlug = slugify(intent).replace(/-/g, "_");
  return `import { NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";

export const runtime = "nodejs";

// Auto-generated route handler. The schema below is intentionally
// permissive — accepts any string fields. Tighten the zod schema
// (require email, validate phone, etc.) by editing this file.
const Body = z.record(z.string(), z.string());

export async function POST(req: Request) {
  let parsed;
  try {
    const body = await req.json();
    parsed = Body.parse(body);
  } catch (err) {
    return NextResponse.json(
      { error: "invalid_input", message: (err as Error).message },
      { status: 400 },
    );
  }
  try {
    const id = await db.insert(${JSON.stringify(intentSlug)}, parsed);
    return NextResponse.json({ ok: true, id }, { status: 201 });
  } catch (err) {
    return NextResponse.json(
      { error: "db_error", message: (err as Error).message },
      { status: 500 },
    );
  }
}
`;
}

// ── Auth.js v5 templates ─────────────────────────────────────────────

function authTs(): string {
  return `// Auto-generated by /api/design/build because the design has
// auth-implying UI (password input, login/signup link, sign-in button).
// Auth.js v5 (next-auth beta) with credentials + magic-link providers.
// OAuth slots (Google, GitHub) activate automatically when the
// corresponding env vars are set in Settings → Secrets.
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";

const oauthProviders = [];
if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
  // Conditionally enable Google OAuth.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const Google = require("next-auth/providers/google").default;
  oauthProviders.push(
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  );
}
if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const GitHub = require("next-auth/providers/github").default;
  oauthProviders.push(
    GitHub({
      clientId: process.env.GITHUB_CLIENT_ID,
      clientSecret: process.env.GITHUB_CLIENT_SECRET,
    }),
  );
}

export const { auth, handlers, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      // STUB: replace with real verification against your users
      // table. The stub lets the flow exercise without a DB lookup
      // so previews work; production must verify against db.
      async authorize(creds) {
        const email = String(creds?.email ?? "").toLowerCase().trim();
        const password = String(creds?.password ?? "");
        if (!email || !password) return null;
        return { id: email, email, name: email.split("@")[0] };
      },
    }),
    ...oauthProviders,
  ],
  session: { strategy: "jwt", maxAge: 60 * 60 * 24 },
});
`;
}

function authHandlerTs(): string {
  return `// Auto-generated. Mounts Auth.js handlers at /api/auth/*.
import { handlers } from "@/auth";
export const { GET, POST } = handlers;
`;
}

function authMiddlewareTs(): string {
  return `// Auto-generated. Protects /protected/* routes by default.
// Edit the matcher below to gate other paths (e.g. /admin, /dashboard).
export { auth as middleware } from "@/auth";

export const config = {
  matcher: ["/protected/:path*"],
};
`;
}

// ── Upload template ──────────────────────────────────────────────────

function uploadRouteTs(): string {
  return `import { NextResponse } from "next/server";
import path from "node:path";
import fs from "node:fs/promises";
import crypto from "node:crypto";

export const runtime = "nodejs";

// Auto-generated. Local-disk storage in dev — files land at
// uploads/<sha256>.<ext> relative to project root, served back via
// /api/upload?id=... below. Production swap to Vercel Blob: replace
// the writeFile + read paths with \`put()\` and \`head()\` from
// "@vercel/blob"; everything else (multipart parsing, response
// shape) stays identical.
//
// Limit: 25MB per upload by default. Override via UPLOAD_MAX_MB.

const MAX_MB = Number(process.env.UPLOAD_MAX_MB ?? "25");
const ROOT = path.join(process.cwd(), "uploads");

export async function POST(req: Request) {
  const ct = req.headers.get("content-type") ?? "";
  if (!ct.startsWith("multipart/form-data")) {
    return NextResponse.json(
      { error: "expected_multipart" },
      { status: 400 },
    );
  }
  const form = await req.formData();
  const file = form.get("file");
  if (!(file instanceof Blob)) {
    return NextResponse.json({ error: "missing_file" }, { status: 400 });
  }
  if (file.size > MAX_MB * 1024 * 1024) {
    return NextResponse.json(
      { error: "too_large", maxMB: MAX_MB },
      { status: 413 },
    );
  }
  const buf = Buffer.from(await file.arrayBuffer());
  const id = crypto.createHash("sha256").update(buf).digest("hex");
  const name = (file as File).name ?? "";
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  const key = id + ext;
  await fs.mkdir(ROOT, { recursive: true });
  await fs.writeFile(path.join(ROOT, key), buf);
  return NextResponse.json({
    ok: true,
    id: key,
    url: \`/api/upload?id=\${encodeURIComponent(key)}\`,
    bytes: buf.length,
  });
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const id = url.searchParams.get("id") ?? "";
  if (!/^[a-f0-9]{64}(\\.[a-zA-Z0-9]{1,8})?$/.test(id)) {
    return NextResponse.json({ error: "bad_id" }, { status: 400 });
  }
  try {
    const data = await fs.readFile(path.join(ROOT, id));
    return new NextResponse(data, {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "public, max-age=31536000, immutable",
      },
    });
  } catch {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
}
`;
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "submit";
}

function gitignoreTxt(): string {
  return `node_modules/
.next/
.turbo/
.cache/
dist/
build/
out/

# Database (sqlite local-dev file; production is Postgres via DATABASE_URL)
data/

# Uploads (local-disk storage in dev; production is Vercel Blob / S3)
uploads/

# Local env
.env.local
.env*.local

# OS
.DS_Store
Thumbs.db

# Jarvis
.jarvis/
`;
}

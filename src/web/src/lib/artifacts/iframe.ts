// Pure builders for the sandboxed preview iframe's srcDoc. Shared by the
// client renderer (components/artifacts/artifact-render.tsx) and the
// server-rendered public page (/a/[token]) so both render identically.
// The import map collapses every React import (bare or esm.sh URL) to ONE
// canonical 18.3.1 instance — mirrors design-preview.tsx's IMPORT_MAP.

export const ARTIFACT_IMPORT_MAP = {
  imports: {
    react: "https://esm.sh/react@18.3.1",
    "react/": "https://esm.sh/react@18.3.1/",
    "react-dom": "https://esm.sh/react-dom@18.3.1?deps=react@18.3.1",
    "react-dom/": "https://esm.sh/react-dom@18.3.1&deps=react@18.3.1/",
    "react/jsx-runtime": "https://esm.sh/react@18.3.1/jsx-runtime",
    "react/jsx-dev-runtime": "https://esm.sh/react@18.3.1/jsx-dev-runtime",
    "react-dom/client":
      "https://esm.sh/react-dom@18.3.1/client?deps=react@18.3.1",
    // Common libraries models reach for (claude.ai pre-bundles a fixed set;
    // we map a superset to esm.sh, all pinned to our React so hooks dedupe).
    // For HTML artifacts that use bare `import` in a module script; React
    // artifacts get the same libs via the bundler's esm.sh rewrite.
    "lucide-react": "https://esm.sh/lucide-react@0.469?deps=react@18.3.1",
    recharts: "https://esm.sh/recharts@2?deps=react@18.3.1",
    "framer-motion": "https://esm.sh/framer-motion@11?deps=react@18.3.1",
    motion: "https://esm.sh/framer-motion@11?deps=react@18.3.1",
    "motion/react": "https://esm.sh/framer-motion@11?deps=react@18.3.1",
    three: "https://esm.sh/three@0.169",
    "three/": "https://esm.sh/three@0.169/",
    "@react-three/fiber":
      "https://esm.sh/@react-three/fiber@8?deps=react@18.3.1,three@0.169",
    "@react-three/drei":
      "https://esm.sh/@react-three/drei@9?deps=react@18.3.1,three@0.169",
    d3: "https://esm.sh/d3@7",
    clsx: "https://esm.sh/clsx@2",
    "date-fns": "https://esm.sh/date-fns@4",
    zustand: "https://esm.sh/zustand@5?deps=react@18.3.1",
    // Common app stacks (Framer Motion above). Pinned to our React where
    // relevant. NOTE: any other bare import the model writes still resolves —
    // the React bundler rewrites ALL bare specifiers to esm.sh; these entries
    // are for HTML artifacts + version pinning.
    "@base-ui/react": "https://esm.sh/@base-ui-components/react?deps=react@18.3.1",
    "@base-ui/react/": "https://esm.sh/@base-ui-components/react/",
    "@base-ui-components/react":
      "https://esm.sh/@base-ui-components/react?deps=react@18.3.1",
    "swagger-ui-react": "https://esm.sh/swagger-ui-react?deps=react@18.3.1",
    "@monaco-editor/react":
      "https://esm.sh/@monaco-editor/react@4?deps=react@18.3.1",
    "monaco-editor": "https://esm.sh/monaco-editor@0.52",
    "monaco-editor/": "https://esm.sh/monaco-editor@0.52/",
    "core-js": "https://esm.sh/core-js@3",
    "core-js/": "https://esm.sh/core-js@3/",
    "@datadog/browser-rum": "https://esm.sh/@datadog/browser-rum@5",
    "@segment/analytics-next": "https://esm.sh/@segment/analytics-next@1",
  },
} as const;

// Tailwind Play CDN — so the Tailwind utility classes models emit (exactly
// like claude.ai's artifacts) actually render styled, with no build step.
const TAILWIND_CDN = `<script src="https://cdn.tailwindcss.com"></script>`;

// AI-powered-apps bridge — mirrors claude.ai's `window.claude.complete`.
// Exposes window.jarvis.{complete, callTool} inside the artifact, RPC'd to
// the parent page over postMessage (the parent — which holds the session —
// proxies to /api/artifacts/{complete,mcp}). Defined before the artifact's
// own script so it's available on load. The parent verifies the message
// came from THIS iframe + caps the call volume (see artifact-render.tsx).
const JARVIS_BRIDGE = `<script>(function(){
  var pending={};
  window.addEventListener("message",function(e){
    var d=e.data; if(!d||!d.__jarvis_rpc_res)return;
    var p=pending[d.id]; if(!p)return; delete pending[d.id];
    if(d.error)p.reject(new Error(d.error)); else p.resolve(d.result);
  });
  function rpc(method,payload){return new Promise(function(res,rej){
    var id=Math.random().toString(36).slice(2)+Date.now();
    pending[id]={resolve:res,reject:rej};
    parent.postMessage({__jarvis_rpc_req:true,id:id,method:method,payload:payload},"*");
  });}
  window.jarvis={
    complete:function(prompt,opts){return rpc("complete",Object.assign({prompt:prompt},opts||{}));},
    callTool:function(server,tool,args){return rpc("mcp",{server:server,tool:tool,args:args||{}});}
  };
})();</script>`;

// Catches runtime + resource-load errors inside the iframe and paints a
// readable overlay instead of failing silently (users can't open the
// iframe's console). Compact version of design-preview.tsx's handler.
const ERROR_OVERLAY = `<script>(function(){
  function show(m){
    var d=document.getElementById('__artifact_err')||document.createElement('div');
    d.id='__artifact_err';
    d.style.cssText='position:fixed;inset:0;z-index:2147483647;background:#190b0b;color:#ffb4b4;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;padding:20px;white-space:pre-wrap;overflow:auto';
    d.textContent='\\u26a0  '+m;
    if(document.body) document.body.appendChild(d);
  }
  window.addEventListener('error',function(e){
    var t=e.target;
    if(t&&t!==window&&(t.tagName==='SCRIPT'||t.tagName==='LINK')){show((t.src||t.href||'(no src)')+' failed to load');return;}
    if(t&&t!==window&&(t.tagName==='IMG'||t.tagName==='VIDEO'||t.tagName==='AUDIO'||t.tagName==='SOURCE'))return;
    var msg=e.message||(e.error&&e.error.message)||'error';
    show(msg+(e.error&&e.error.stack?'\\n\\n'+e.error.stack:''));
  },true);
  window.addEventListener('unhandledrejection',function(e){
    var r=e.reason; show('unhandled rejection: '+((r&&(r.stack||r.message))||String(r)));
  });
})();</script>`;

const importMapTag = `<script type="importmap">${JSON.stringify(
  ARTIFACT_IMPORT_MAP,
)}</script>`;

// Embedding bundled JS in a <script> needs </script> sequences neutralized
// so a string literal in the code can't close the tag early.
function escapeScript(js: string): string {
  return js.replace(/<\/(script)/gi, "<\\/$1");
}

export function buildReactDoc(bundledJs: string): string {
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
${TAILWIND_CDN}
${JARVIS_BRIDGE}
${importMapTag}
<style>html,body{margin:0;padding:0}body{font-family:ui-sans-serif,system-ui,-apple-system,sans-serif}</style>
${ERROR_OVERLAY}
</head><body><div id="root"></div>
<script type="module">${escapeScript(bundledJs)}</script>
</body></html>`;
}

export function buildHtmlDoc(html: string): string {
  // Tailwind + import map injected so HTML artifacts that lean on Tailwind
  // classes or bare `import` module scripts render without a build step.
  // (A self-contained doc with its own CSS is unaffected by Tailwind's CDN.)
  const head = `${TAILWIND_CDN}${JARVIS_BRIDGE}${importMapTag}${ERROR_OVERLAY}`;
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/(<head[^>]*>)/i, `$1${head}`);
  }
  if (/<html[^>]*>/i.test(html)) {
    return html.replace(/(<html[^>]*>)/i, `$1<head>${head}</head>`);
  }
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${head}</head><body>${html}</body></html>`;
}

export function buildSvgDoc(svg: string): string {
  return `<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%}body{display:flex;align-items:center;justify-content:center;background:#fff}svg{max-width:100%;max-height:100%;height:auto}</style>
</head><body>${svg}</body></html>`;
}

// SECURITY: deliberately NO `allow-same-origin`. A srcdoc iframe with
// allow-scripts + allow-same-origin runs in the PARENT's origin and could
// read JARVIS cookies / call authed APIs (XSS / account takeover). Without
// it the iframe gets a unique opaque origin — isolated from the app. The
// window.jarvis bridge works over postMessage (cross-origin), esm.sh/Tailwind
// load fine (CORS), so nothing here needs same-origin. Trade-off: artifacts
// can't use localStorage/cookies (acceptable; would need a dedicated origin).
export const ARTIFACT_IFRAME_SANDBOX =
  "allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox";

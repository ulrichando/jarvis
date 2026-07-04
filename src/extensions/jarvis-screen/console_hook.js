// console_hook.js — runs in the page's MAIN world (declared world:"MAIN" at
// document_start) so it can see the page's real console. It wraps the console
// methods and forwards each message to the isolated content script via
// window.postMessage; content.js buffers them for the read_console action.
// (Isolated content scripts share the DOM but NOT the page's JS globals, so a
// MAIN-world hook + postMessage is the only CSP-safe way to read the console.)
//
// The isolated content script only loads at document_idle, so messages logged
// during early page load would be missed. To close that gap, the hook keeps its
// own buffer and REPLAYS it when content.js connects (a "jarvis-console-sync"
// message). Each message carries a monotonic `seq` so content.js can dedupe a
// live message that also appears in the replay.
(function () {
  if (window.__jarvisConsoleHooked) return;
  window.__jarvisConsoleHooked = true;
  const buf = [];
  let seq = 0;
  function record(level, text) {
    const m = { source: "jarvis-console", level, text, seq: ++seq };
    buf.push(m);
    if (buf.length > 200) buf.shift();
    try { window.postMessage(m, "*"); } catch { /* ignore */ }
  }
  for (const level of ["log", "info", "warn", "error"]) {
    const orig = typeof console[level] === "function" ? console[level].bind(console) : function () {};
    console[level] = function (...args) {
      try {
        const text = args
          .map((a) => { try { return typeof a === "string" ? a : JSON.stringify(a); } catch { return String(a); } })
          .join(" ")
          .slice(0, 500);
        record(level, text);
      } catch { /* never break the page's console */ }
      return orig(...args);
    };
  }
  // content.js asks for a replay once it starts listening — resend the buffer.
  window.addEventListener("message", (e) => {
    if ((!e.source || e.source === window) && e.data && e.data.source === "jarvis-console-sync") {
      for (const m of buf) { try { window.postMessage(m, "*"); } catch { /* ignore */ } }
    }
  });
})();

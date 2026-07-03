/** @jest-environment jsdom */
// Verifies the console-capture handshake: the MAIN-world hook buffers from
// document_start and REPLAYS to the isolated listener on sync, so page-load
// messages aren't lost — and a live message that also appears in the replay is
// deduped by seq. (jsdom collapses the two worlds to one window, so postMessage
// loops back, which is exactly what we need to exercise the protocol.)
// Install the hook on this window (wraps console + adds the replay listener).
// require runs console_hook.js's IIFE against jsdom's global `window` — no eval.
require("../console_hook.js");

test("replays pre-listener console messages and dedupes the live one", async () => {
  // Logged BEFORE the isolated listener exists → only in the hook's buffer.
  console.log("EARLY-1");
  console.log("EARLY-2");

  // The isolated-world buffer listener (mirrors content.js).
  const buf = [];
  const seen = new Set();
  window.addEventListener("message", (e) => {
    if ((e.source && e.source !== window) || !e.data || e.data.source !== "jarvis-console") return;
    const m = e.data;
    if (typeof m.seq === "number") { if (seen.has(m.seq)) return; seen.add(m.seq); }
    buf.push(m.text);
  });

  window.postMessage({ source: "jarvis-console-sync" }, "*"); // request replay
  console.log("LATE-1"); // live (also buffered → will appear in the replay)

  await new Promise((r) => setTimeout(r, 60)); // flush async message events

  expect(buf).toContain("EARLY-1"); // captured despite being pre-listener
  expect(buf).toContain("EARLY-2");
  expect(buf).toContain("LATE-1");
  expect(buf.filter((x) => x === "LATE-1").length).toBe(1); // replay didn't dupe it
  expect(buf.filter((x) => x === "EARLY-1").length).toBe(1);
});

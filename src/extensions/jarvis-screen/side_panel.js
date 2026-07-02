// side_panel.js — connection status, token pairing, and a recent-action log for
// jarvis-screen. Talks to the background service worker via runtime messages.

const $ = (id) => document.getElementById(id);

const STATE = {
  connected:    { cls: "ok",      label: "Connected",       reason: "Jarvis can act on the current tab." },
  connecting:   { cls: "pending", label: "Connecting…",     reason: "Reaching the local bridge." },
  disconnected: { cls: "bad",     label: "Disconnected",    reason: "Bridge unreachable — is the Jarvis desktop app running?" },
  no_token:     { cls: "warn",    label: "Needs a token",   reason: "Paste the bridge token below to connect." },
  auth_error:   { cls: "bad",     label: "Auth failed",     reason: "The token was rejected by the bridge. Check it and reconnect." },
};

function render(status, lastActions) {
  const s = STATE[status] || STATE.disconnected;
  $("dot").className = `dot ${s.cls}`;
  $("state").textContent = s.label;
  $("reason").textContent = s.reason;

  const log = $("log");
  log.textContent = "";
  if (!lastActions || lastActions.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nothing yet.";
    log.appendChild(li);
    return;
  }
  // Build rows with textContent only — action names come off the command
  // stream, so never interpolate them into innerHTML (XSS surface).
  for (const a of lastActions) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "a";
    name.textContent = a.action || "?";
    const meta = document.createElement("span");
    meta.className = a.ok ? "ok" : "no";
    meta.textContent = `${a.ok ? "ok" : "fail"} · ${new Date(a.ts).toLocaleTimeString()}`;
    li.append(name, meta);
    log.appendChild(li);
  }
}

async function refresh() {
  try {
    const res = await chrome.runtime.sendMessage({ type: "jarvis_get_status" });
    if (res) render(res.status, res.lastActions);
  } catch { /* SW may be spinning up */ }
}

// Live updates pushed by the background worker.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "jarvis_status") render(msg.status, msg.lastActions);
});

$("save").addEventListener("click", async () => {
  const token = $("token").value.trim();
  if (!token) return;
  await chrome.storage.local.set({ bridge_token: token });
  // storage.onChanged in the background triggers a reconnect.
  $("token").value = "";
  setTimeout(refresh, 400);
});

$("reconnect").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "jarvis_reconnect" }).catch(() => {});
  setTimeout(refresh, 400);
});

// Prefill nothing (token is write-only for safety); just show current status.
(async () => {
  const { bridge_token } = await chrome.storage.local.get("bridge_token");
  if (bridge_token) $("token").placeholder = "•••••• (saved) — paste to replace";
  refresh();
})();
setInterval(refresh, 5000);

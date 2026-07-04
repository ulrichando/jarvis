// authorize.js — first-run consent screen for Jarvis in Chrome. Opened in a
// tab by background.js on install. "Authorize" logs in to your Jarvis web
// account (Chrome identity flow) and receives the bridge credential — no token
// paste — then hands off to the side panel. "Decline" just closes.

const $ = (id) => document.getElementById(id);

function showError(msg) {
  let e = $("authErr");
  if (!e) {
    e = document.createElement("p");
    e.id = "authErr";
    e.style.cssText = "color:oklch(0.68 0.19 25);font-size:13px;text-align:center;margin:12px 0 0";
    $("decline").after(e);
  }
  e.textContent = msg;
}

$("authorize").addEventListener("click", async () => {
  const btn = $("authorize");
  btn.disabled = true;
  btn.textContent = "Connecting…";
  const res = await chrome.runtime
    .sendMessage({ type: "jarvis_login" })
    .catch(() => ({ ok: false, error: "extension error" }));
  if (!res || !res.ok) {
    btn.disabled = false;
    btn.textContent = "Authorize";
    showError(res && res.error ? `Couldn't connect: ${res.error}` : "Login failed — try again.");
    return;
  }
  chrome.storage.local.set({ authorized: true, authorizedAt: Date.now() });
  // Continue in the side panel. sidePanel.open() wants a window id; a lost
  // gesture or old Chrome just means the user opens it from the toolbar icon.
  try {
    const win = await chrome.windows.getCurrent();
    await chrome.sidePanel.open({ windowId: win.id });
  } catch { /* toolbar icon still opens it */ }
  window.close();
});

$("decline").addEventListener("click", async () => {
  await chrome.storage.local.set({ authorized: false });
  window.close();
});

// settings.js — the full-page Jarvis settings (options_ui, opens as a tab).
// An extension page has direct chrome.storage access, so every section reads/
// writes the same keys the panel + background already use (site_permissions,
// shortcuts, permissionMode, …). No bridge round-trips.
const $ = (id) => document.getElementById(id);
const DEFAULT_PERMS = { blocked: [], allowed: [], blockCategories: false };
const DEFAULT_SHORTCUTS = [
  { name: "summarize-page", prompt: "Summarize this page in a few concise bullet points." },
  { name: "extract-links", prompt: "List the main links on this page with their text." },
];

// ── Nav ──────────────────────────────────────────────────────────────────
document.querySelectorAll(".navitem").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".navitem").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll("main section").forEach((s) => (s.hidden = s.dataset.sec !== b.dataset.sec));
  }));

// ── Account + logout ─────────────────────────────────────────────────────
chrome.storage.local.get(["account_email", "bridge_token"]).then(({ account_email, bridge_token }) => {
  // Signed-in state is the presence of the bridge credential; account_email is
  // just a nicer label when the account has one (some accounts have no email,
  // so keying the badge off the email alone falsely showed "Not signed in").
  $("acct").textContent = account_email || (bridge_token ? "Signed in" : "Not signed in");
});
$("logout").addEventListener("click", async () => {
  await chrome.storage.local.remove(["bridge_token", "account_email"]);
  chrome.runtime.sendMessage({ type: "jarvis_logout" }).catch(() => {});
  $("acct").textContent = "Not signed in";
});

// ── Notifications ────────────────────────────────────────────────────────
const notif = $("notifToggle");
const paintNotif = () => { $("notifSub").textContent = notif.checked ? "On" : "Off"; };
chrome.storage.local.get("notifyOnComplete").then(({ notifyOnComplete }) => { notif.checked = !!notifyOnComplete; paintNotif(); });
notif.addEventListener("change", () => { chrome.storage.local.set({ notifyOnComplete: notif.checked }); paintNotif(); });

// ── Microphone (real grant for voice dictation in the composer) ───────────
chrome.storage.local.get("micGranted").then(({ micGranted }) => { if (micGranted) { $("micState").textContent = "Microphone enabled ✓"; $("micBtn").disabled = true; } });
$("micBtn").addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop()); // we only needed the grant
    await chrome.storage.local.set({ micGranted: true });
    $("micState").textContent = "Microphone enabled ✓"; $("micBtn").disabled = true;
  } catch { $("micState").textContent = "Microphone permission was blocked."; }
});

// ── Sites (approved / blocked) ───────────────────────────────────────────
async function getPerms() { const { site_permissions } = await chrome.storage.local.get("site_permissions"); return { ...DEFAULT_PERMS, ...(site_permissions || {}) }; }
async function setPerms(patch) { const next = { ...(await getPerms()), ...patch }; await chrome.storage.local.set({ site_permissions: next }); return next; }
function renderList(container, hosts, emptyMsg, onRemove) {
  container.replaceChildren();
  if (!hosts.length) { const d = document.createElement("div"); d.className = "muted"; d.textContent = emptyMsg; container.appendChild(d); return; }
  for (const h of hosts) {
    const row = document.createElement("div"); row.className = "site";
    const s = document.createElement("span"); s.textContent = h;
    const b = document.createElement("button"); b.className = "btn ghost sm"; b.textContent = "Remove"; b.addEventListener("click", () => onRemove(h));
    row.append(s, b); container.appendChild(row);
  }
}
async function renderSites() {
  const p = await getPerms();
  renderList($("approved"), p.allowed, "No sites approved yet.", (h) => setPerms({ allowed: p.allowed.filter((x) => x !== h) }).then(renderSites));
  renderList($("blocked"), p.blocked, "No blocked sites.", (h) => setPerms({ blocked: p.blocked.filter((x) => x !== h) }).then(renderSites));
}

// ── Shortcuts ────────────────────────────────────────────────────────────
async function getShortcuts() { const { shortcuts } = await chrome.storage.local.get("shortcuts"); return Array.isArray(shortcuts) ? shortcuts : DEFAULT_SHORTCUTS; }
async function renderShortcuts() {
  const list = await getShortcuts(); const c = $("shortcutList"); c.replaceChildren();
  if (!list.length) { const d = document.createElement("div"); d.className = "muted"; d.textContent = "No shortcuts yet."; c.appendChild(d); return; }
  for (const s of list) {
    const row = document.createElement("div"); row.className = "site";
    const label = document.createElement("span");
    const k = document.createElement("b"); k.style.color = "var(--primary)"; k.textContent = "/" + s.name;
    const p = document.createElement("span"); p.className = "muted"; p.style.marginLeft = "10px"; p.textContent = s.prompt.slice(0, 70);
    label.append(k, p);
    const b = document.createElement("button"); b.className = "btn ghost sm"; b.textContent = "Remove";
    b.addEventListener("click", async () => { await chrome.storage.local.set({ shortcuts: (await getShortcuts()).filter((x) => x.name !== s.name) }); renderShortcuts(); });
    row.append(label, b); c.appendChild(row);
  }
}
$("scAdd").addEventListener("click", async () => {
  const name = $("scName").value.trim().replace(/\s+/g, "-").toLowerCase();
  const prompt = $("scPrompt").value.trim();
  if (!name || !prompt) return;
  const list = (await getShortcuts()).filter((x) => x.name !== name); list.push({ name, prompt });
  await chrome.storage.local.set({ shortcuts: list });
  $("scName").value = ""; $("scPrompt").value = ""; renderShortcuts();
});

// ── Options ──────────────────────────────────────────────────────────────
const modeSel = $("modeSel");
chrome.storage.local.get("permissionMode").then(({ permissionMode }) => { modeSel.value = permissionMode === "auto" ? "auto" : "ask"; });
modeSel.addEventListener("change", () => chrome.storage.local.set({ permissionMode: modeSel.value }));

const autoGroup = $("autoGroupToggle");
chrome.storage.local.get("autoGroup").then(({ autoGroup: ag }) => { autoGroup.checked = ag !== false; }); // default on
autoGroup.addEventListener("change", () => chrome.storage.local.set({ autoGroup: autoGroup.checked }));

const catToggle = $("catToggle");
getPerms().then((p) => { catToggle.checked = p.blockCategories === true; });
catToggle.addEventListener("change", () => setPerms({ blockCategories: catToggle.checked }));

renderSites();
renderShortcuts();

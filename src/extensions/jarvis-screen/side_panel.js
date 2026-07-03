// side_panel.js — Jarvis in Chrome side panel: onboarding stepper → chat
// interface. Onboarding advances through beta → automate → tabgroup →
// shortcuts, then reveals the main chat. Chat rides the background service
// worker's bridge WebSocket (query → chat_response). Connection status + token
// pairing are surfaced inline. Acting on the page (the agent loop) comes later;
// this pass is the UI + a talking chat.

const $ = (id) => document.getElementById(id);
const views = [...document.querySelectorAll("[data-view]")];

function show(view) {
  for (const el of views) el.hidden = el.dataset.view !== view;
  // NOTE: do NOT auto-size the textarea here — on panel open the layout isn't
  // settled, so scrollHeight reads bogus-large and pins a tall inline height.
  // CSS gives the single-line height; sizeInput() only grows it on real input.
  if (view === "main") refreshStatus();
}

// ── Onboarding ─────────────────────────────────────────────────────────
document.querySelectorAll("[data-next]").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.next)));

document.querySelector("[data-finish]").addEventListener("click", () => {
  chrome.storage.local.set({ onboardingComplete: true });
  show("main");
});

document.querySelector("[data-learn]")?.addEventListener("click", (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: chrome.runtime.getURL("README.md") }).catch(() => {});
});

// ── Connection status + token pairing ──────────────────────────────────
const STATE = {
  connected:    { cls: "ok",   text: "Connected — Jarvis can act on this tab." },
  connecting:   { cls: "warn", text: "Connecting to the local bridge…" },
  disconnected: { cls: "bad",  text: "Bridge unreachable — is the Jarvis desktop app running?" },
  no_token:     { cls: "warn", text: "Log in to your Jarvis account to connect." },
  auth_error:   { cls: "bad",  text: "Sign-in expired — log in again." },
};

function renderStatus(status) {
  const s = STATE[status] || STATE.disconnected;
  $("statusDot").className = s.cls;
  $("statusText").textContent = s.text;
  // Connected → hide the login banner, let the chat breathe, load the models.
  $("connectBox").hidden = status === "connected";
  if (status === "connected" && !MODELS.length) loadModels();
}

async function refreshStatus() {
  try {
    const res = await chrome.runtime.sendMessage({ type: "jarvis_get_status" });
    if (res) renderStatus(res.status);
  } catch { /* SW spinning up */ }
}

$("loginBtn").addEventListener("click", async () => {
  const btn = $("loginBtn");
  btn.disabled = true;
  btn.textContent = "Connecting…";
  // Background runs the web-account login (Chrome identity flow) and stores the
  // minted credential; then it reconnects. No token paste.
  const res = await chrome.runtime
    .sendMessage({ type: "jarvis_login" })
    .catch((e) => ({ ok: false, error: String((e && e.message) || e) }));
  btn.disabled = false;
  btn.textContent = "Log in to Jarvis";
  if (res && res.ok) { setTimeout(refreshStatus, 400); return; }
  // Surface WHY instead of silently resetting (e.g. web route not deployed,
  // identity permission not granted, or the login window was closed).
  $("statusDot").className = "bad";
  $("statusText").textContent = res && res.error
    ? `Login failed: ${res.error}`
    : "Login didn't complete — is the Jarvis web app reachable?";
});

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg) return;
  if (msg.type === "jarvis_status") renderStatus(msg.status);
  else if (msg.type === "jarvis_chat_status") setThinking(msg.status === "thinking");
  else if (msg.type === "jarvis_chat_response") receiveReply(msg.text);
  else if (msg.type === "jarvis_agent_event") handleAgentEvent(msg.event);
});

// ── Chat ────────────────────────────────────────────────────────────────
const stream = $("stream");
const input = $("input");
let thinkingEl = null;

function sizeInput() {
  input.style.height = "auto";
  input.style.height = Math.min(Math.max(input.scrollHeight, 21), 108) + "px";
  $("sendBtn").disabled = input.value.trim() === "";
}

function addMsg(text, who) {
  $("emptyState")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${who}`;
  el.textContent = text;
  stream.appendChild(el);
  stream.scrollTop = stream.scrollHeight;
  return el;
}

function setThinking(on) {
  if (on && !thinkingEl) {
    thinkingEl = addMsg("Thinking…", "bot thinking");
  } else if (!on && thinkingEl) {
    thinkingEl.remove();
    thinkingEl = null;
  }
}

function receiveReply(text) {
  setThinking(false);
  addMsg(text, "bot");
}

// ── Browser-agent step stream ───────────────────────────────────────────
let lastStep = null;
function addStep(label) {
  $("emptyState")?.remove();
  const el = document.createElement("div");
  el.className = "step-row";
  const dot = document.createElement("span"); dot.className = "step-dot";
  const txt = document.createElement("span"); txt.textContent = label; // agent text → textContent (no XSS)
  el.append(dot, txt);
  stream.appendChild(el);
  stream.scrollTop = stream.scrollHeight;
  return el;
}
function handleAgentEvent(ev) {
  if (!ev) return;
  if (ev.type === "action") { setThinking(false); lastStep = addStep(ev.label); }
  else if (ev.type === "action_result") { if (lastStep) { lastStep.classList.add(ev.ok ? "ok" : "fail"); lastStep = null; } }
  else if (ev.type === "approval") { renderApproval(ev.approvalId, ev.label); }
  // "text" events are intermediate reasoning; the final chat_response is the answer.
}
function renderApproval(approvalId, label) {
  setThinking(false);
  $("emptyState")?.remove();
  const card = document.createElement("div");
  card.className = "approval";
  const t = document.createElement("div"); t.className = "at"; t.textContent = `Approve this action? ${label}`;
  const row = document.createElement("div"); row.className = "arow";
  const yes = document.createElement("button"); yes.className = "btn btn-primary"; yes.textContent = "Approve";
  const no = document.createElement("button"); no.className = "btn"; no.textContent = "Deny";
  no.style.cssText = "background:var(--panel);color:var(--fg)";
  const decide = (approved) => {
    chrome.runtime.sendMessage({ type: "jarvis_agent_approval", approvalId, approved }).catch(() => {});
    card.remove();
    if (approved) setThinking(true);
  };
  yes.addEventListener("click", () => decide(true));
  no.addEventListener("click", () => decide(false));
  row.append(yes, no); card.append(t, row);
  stream.appendChild(card);
  stream.scrollTop = stream.scrollHeight;
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  addMsg(text, "user");
  input.value = "";
  sizeInput();
  setThinking(true);
  try {
    // permMode ("ask"/"auto") drives whether the agent pauses for approval.
    await chrome.runtime.sendMessage({ type: "jarvis_chat", text, mode: permMode });
  } catch {
    receiveReply("Couldn't reach the Jarvis bridge. Is the desktop app running?");
  }
}

input.addEventListener("input", sizeInput);
input.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { hideShortcuts(); return; }
  if (e.key === "Enter" && !e.shiftKey) {
    if (!$("scMenu").hidden) {
      const q = input.value.slice(1).toLowerCase();
      const first = SHORTCUTS.filter((s) => s.name.toLowerCase().includes(q))[0];
      if (first) { e.preventDefault(); useShortcut(first); return; }
    }
    e.preventDefault(); send();
  }
});
$("sendBtn").addEventListener("click", send);

// ── Shortcuts (type / in the composer) ─────────────────────────────────
let SHORTCUTS = [];
const DEFAULT_SHORTCUTS = [
  { name: "summarize-page", prompt: "Summarize this page in a few concise bullet points." },
  { name: "extract-links", prompt: "List the main links on this page with their text." },
];
async function loadShortcuts() {
  const { shortcuts } = await chrome.storage.local.get("shortcuts");
  if (Array.isArray(shortcuts)) SHORTCUTS = shortcuts;
  else { SHORTCUTS = DEFAULT_SHORTCUTS; chrome.storage.local.set({ shortcuts: SHORTCUTS }); }
}
function hideShortcuts() { const m = $("scMenu"); m.hidden = true; m.replaceChildren(); }
function useShortcut(s) { input.value = s.prompt; hideShortcuts(); sizeInput(); input.focus(); }
function scRow(cls, key, text, onClick) {
  const row = document.createElement("div"); row.className = "sc" + (cls ? " " + cls : "");
  const k = document.createElement("span"); k.className = "k"; k.textContent = key;
  const p = document.createElement("span"); p.className = "p"; p.textContent = text;
  row.append(k, p); row.addEventListener("click", onClick); return row;
}
function refreshShortcutMenu() {
  const m = $("scMenu");
  if (!input.value.startsWith("/")) { hideShortcuts(); return; }
  const q = input.value.slice(1).toLowerCase();
  m.replaceChildren();
  for (const s of SHORTCUTS.filter((s) => s.name.toLowerCase().includes(q))) {
    m.appendChild(scRow("", "/" + s.name, s.prompt, () => useShortcut(s)));
  }
  m.appendChild(scRow("add", "＋", "Create new shortcut", showShortcutForm));
  m.hidden = false;
}
function showShortcutForm() {
  const m = $("scMenu"); m.replaceChildren();
  const form = document.createElement("div"); form.className = "sc-form";
  const name = document.createElement("input"); name.placeholder = "name (e.g. unsubscribe)";
  const promptI = document.createElement("input"); promptI.placeholder = "the instruction to send";
  const frow = document.createElement("div"); frow.className = "frow";
  const cancel = document.createElement("button"); cancel.className = "btn"; cancel.textContent = "Cancel"; cancel.style.cssText = "padding:5px 12px;background:var(--card);color:var(--fg);font-size:12.5px;border-radius:8px";
  const save = document.createElement("button"); save.className = "btn btn-primary"; save.textContent = "Save"; save.style.cssText = "padding:5px 12px;font-size:12.5px;border-radius:8px";
  cancel.addEventListener("click", () => { hideShortcuts(); input.value = ""; sizeInput(); });
  save.addEventListener("click", async () => {
    const n = name.value.trim().replace(/\s+/g, "-").toLowerCase();
    const p = promptI.value.trim();
    if (!n || !p) return;
    SHORTCUTS = [...SHORTCUTS.filter((x) => x.name !== n), { name: n, prompt: p }];
    await chrome.storage.local.set({ shortcuts: SHORTCUTS });
    hideShortcuts(); input.value = ""; sizeInput();
  });
  frow.append(cancel, save); form.append(name, promptI, frow);
  m.appendChild(form); m.hidden = false; name.focus();
}
input.addEventListener("input", refreshShortcutMenu);

$("newChat").addEventListener("click", () => {
  stream.replaceChildren();
  thinkingEl = null;
  const empty = document.createElement("div");
  empty.className = "empty";
  const s1 = document.createElement("strong"); s1.textContent = "New chat";
  const s2 = document.createElement("span"); s2.textContent = "Ask Jarvis about this page, or give it a task.";
  empty.append(s1, s2);
  stream.appendChild(empty);
});

// ── Popover menus (permission mode, 3-dot, model) ───────────────────────
function openPop(anchor, items, { align = "left" } = {}) {
  closePop();
  const backdrop = document.createElement("div");
  backdrop.className = "backdrop";
  backdrop.addEventListener("click", closePop);
  const pop = document.createElement("div");
  pop.className = "pop";
  pop.id = "activePop";
  for (const it of items) {
    if (it.sep) continue;
    const b = document.createElement("button");
    b.className = "mi" + (it.checked !== undefined ? " check" : "");
    // Icons/chevrons/ticks are trusted static SVG (innerHTML); the label is
    // set via textContent so a dynamic value can never inject markup.
    if (it.icon) { const s = document.createElement("span"); s.style.display = "inline-flex"; s.innerHTML = it.icon; b.appendChild(s); }
    const lbl = document.createElement("span"); lbl.textContent = it.label; b.appendChild(lbl);
    if (it.chev) { const c = document.createElement("span"); c.className = "chev"; c.innerHTML = CHEV_SVG; b.appendChild(c); }
    if (it.checked) { const t = document.createElement("span"); t.className = "tick"; t.innerHTML = TICK_SVG; b.appendChild(t); }
    b.addEventListener("click", () => { closePop(); it.onClick?.(); });
    pop.appendChild(b);
  }
  document.body.append(backdrop, pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 8) + "px";
  if (align === "right") pop.style.right = (window.innerWidth - r.right) + "px";
  else pop.style.left = Math.max(8, r.left) + "px";
  // Menus that open upward from the composer:
  if (r.bottom + pop.offsetHeight > window.innerHeight - 8) {
    pop.style.top = (r.top - pop.offsetHeight - 6) + "px";
  }
}
function closePop() {
  $("activePop")?.remove();
  document.querySelector(".backdrop")?.remove();
}

const icon = (p) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">${p}</svg>`;
const CHEV_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const TICK_SVG = '<svg width="15" height="15" viewBox="0 0 20 20" fill="none"><path d="M4 10.5l4 4 8-9" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// Permission mode
let permMode = "ask"; // ask | auto
const PERM = { ask: "Ask before acting", auto: "Act on its own" };
function setPerm(mode) { permMode = mode; $("permLabel").textContent = PERM[mode]; chrome.storage.local.set({ permissionMode: mode }); }
$("permBtn").addEventListener("click", () => openPop($("permBtn"), [
  { label: PERM.ask,  checked: permMode === "ask",  onClick: () => setPerm("ask") },
  { label: PERM.auto, checked: permMode === "auto", onClick: () => setPerm("auto") },
]));

// 3-dot menu
$("menuBtn").addEventListener("click", () => openPop($("menuBtn"), [
  { label: "Convert to task", icon: icon('<circle cx="12" cy="12" r="9"/><path d="M12 8v4l2.5 2" stroke-linecap="round"/>'), onClick: showTaskForm },
  { label: "Scheduled tasks", icon: icon('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" stroke-linecap="round"/>'), onClick: showTaskList },
  { label: recording ? "Stop recording" : "Record workflow", icon: icon(recording ? '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>' : '<circle cx="12" cy="12" r="5" fill="currentColor"/>'), onClick: toggleRecord },
  { label: "Workflows",       icon: icon('<path d="M6 4l14 8-14 8V4Z" stroke-linejoin="round"/>'), onClick: showWorkflows },
  { label: "Settings",        icon: icon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.2A1.6 1.6 0 0 0 6.8 19l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 4 13.6H4a2 2 0 1 1 0-4h.2A1.6 1.6 0 0 0 5.6 7l-.1-.1A2 2 0 1 1 8.3 4.1l.1.1A1.6 1.6 0 0 0 11 3.3V3a2 2 0 1 1 4 0v.2A1.6 1.6 0 0 0 17.7 4.6l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.6 1.6 0 0 0 20.7 10H21a2 2 0 1 1 0 4h-.2Z"/>'), onClick: showSettings },
  { label: "Language", chev: true, icon: icon('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/>'), onClick: () => note("Language — coming soon") },
], { align: "right" }));

// Model selector — reflects the bridge's model list (GET /api/models) and sets
// the active model (POST /api/model), both proxied through the background.
let MODELS = [];
let ACTIVE_MODEL = "";
function prettyModel(id) {
  return String(id || "")
    .replace(/-\d{8}$/, "")                       // drop a trailing -YYYYMMDD date
    .replace(/^(claude-|anthropic\.|us\.anthropic\.)/, "")
    .replace(/-(\d+)-(\d+)/, " $1.$2")            // -4-6 → " 4.6"
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim() || "Jarvis";
}
async function loadModels() {
  const res = await chrome.runtime.sendMessage({ type: "jarvis_models" }).catch(() => null);
  if (!res || !res.ok || !Array.isArray(res.models)) return;
  MODELS = res.models.map((m) => (typeof m === "string" ? m : m.name)).filter(Boolean);
  ACTIVE_MODEL = res.active || MODELS[0] || "";
  if (ACTIVE_MODEL) $("modelLabel").textContent = prettyModel(ACTIVE_MODEL);
}
$("modelBtn").addEventListener("click", () => {
  if (!MODELS.length) { note("Log in to load models"); return; }
  openPop($("modelBtn"), MODELS.map((id) => ({
    label: prettyModel(id),
    checked: id === ACTIVE_MODEL,
    onClick: async () => {
      ACTIVE_MODEL = id;
      $("modelLabel").textContent = prettyModel(id);
      await chrome.runtime.sendMessage({ type: "jarvis_set_model", model: id }).catch(() => {});
    },
  })));
});

// Ephemeral inline note for not-yet-wired actions.
let noteTimer = null;
function note(text) {
  clearTimeout(noteTimer);
  let n = $("note");
  if (!n) { n = document.createElement("div"); n.id = "note"; n.className = "disclaimer"; n.style.color = "var(--muted-fg)"; $("stream").after(n); }
  n.textContent = text;
  noteTimer = setTimeout(() => n.remove(), 2200);
}

// ── Scheduled tasks (Convert to task) ──────────────────────────────────
function showTaskForm() {
  $("emptyState")?.remove();
  const lastUser = [...stream.querySelectorAll(".msg.user")].pop();
  const card = document.createElement("div"); card.className = "approval";
  const t = document.createElement("div"); t.className = "at"; t.textContent = "Schedule a recurring task";
  const ta = document.createElement("textarea"); ta.className = "task-ta"; ta.placeholder = "What should Jarvis do on each run?";
  if (lastUser) ta.value = lastUser.textContent;
  const sel = document.createElement("select"); sel.className = "task-sel";
  for (const [v, label] of [["hourly", "Every hour"], ["daily", "Every day"], ["weekly", "Every week"]]) {
    const o = document.createElement("option"); o.value = v; o.textContent = label; sel.appendChild(o);
  }
  sel.value = "daily";
  const frow = document.createElement("div"); frow.className = "arow"; frow.style.marginTop = "9px";
  const cancel = document.createElement("button"); cancel.className = "btn"; cancel.textContent = "Cancel"; cancel.style.cssText = "background:var(--panel);color:var(--fg)";
  const save = document.createElement("button"); save.className = "btn btn-primary"; save.textContent = "Save task";
  cancel.addEventListener("click", () => card.remove());
  save.addEventListener("click", async () => {
    const prompt = ta.value.trim(); if (!prompt) return;
    const res = await chrome.runtime.sendMessage({ type: "jarvis_task_add", task: { prompt, cadence: sel.value } }).catch(() => ({ ok: false }));
    card.remove();
    addMsg(res && res.ok
      ? `✓ Scheduled — I'll run this ${sel.options[sel.selectedIndex].textContent.toLowerCase()}.`
      : "Couldn't schedule that task.", "bot");
  });
  frow.append(cancel, save);
  card.append(t, ta, sel, frow);
  stream.appendChild(card); stream.scrollTop = stream.scrollHeight;
}
function showTaskList() {
  chrome.runtime.sendMessage({ type: "jarvis_task_list" }).then((res) => {
    $("emptyState")?.remove();
    const tasks = (res && res.tasks) || [];
    const card = document.createElement("div"); card.className = "approval";
    const t = document.createElement("div"); t.className = "at"; t.textContent = tasks.length ? "Scheduled tasks" : "No scheduled tasks yet.";
    card.appendChild(t);
    for (const task of tasks) {
      const row = document.createElement("div"); row.className = "task-row";
      const txt = document.createElement("span"); txt.className = "tx"; txt.textContent = `${task.prompt.slice(0, 44)} · ${task.cadence}`;
      const del = document.createElement("button"); del.className = "iconbtn"; del.title = "Delete"; del.textContent = "✕";
      del.addEventListener("click", async () => { await chrome.runtime.sendMessage({ type: "jarvis_task_delete", id: task.id }).catch(() => {}); row.remove(); });
      row.append(txt, del); card.appendChild(row);
    }
    stream.appendChild(card); stream.scrollTop = stream.scrollHeight;
  }).catch(() => {});
}

// ── Settings: site permissions (#3 allow/block, #4 blocked categories) ──
async function showSettings() {
  const res = await chrome.runtime.sendMessage({ type: "jarvis_site_perms_get" }).catch(() => null);
  const perms = (res && res.perms) || { blocked: [], allowed: [], blockCategories: false };
  const host = (res && res.host) || "";
  const decision = (res && res.decision) || { allow: true };
  $("emptyState")?.remove();
  const card = document.createElement("div"); card.className = "approval";
  const t = document.createElement("div"); t.className = "at"; t.textContent = "Site permissions"; card.appendChild(t);

  const setPerms = async (patch) => {
    const r = await chrome.runtime.sendMessage({ type: "jarvis_site_perms_set", patch }).catch(() => null);
    if (r && r.ok) { card.remove(); showSettings(); } // re-render with fresh state
  };

  // Current site — one contextual action based on its state.
  if (host) {
    const row = document.createElement("div"); row.className = "task-row";
    const isAllowed = perms.allowed.includes(host);
    const isBlocked = perms.blocked.includes(host);
    const label = document.createElement("span"); label.className = "tx";
    label.textContent = host + (isAllowed ? " · allowed (override)" : isBlocked ? " · blocked" : decision.allow ? "" : " · blocked by default");
    const btn = document.createElement("button"); btn.className = "btn"; btn.style.cssText = "background:var(--panel);color:var(--fg)";
    if (isAllowed) { btn.textContent = "Reset"; btn.onclick = () => setPerms({ allowed: perms.allowed.filter((h) => h !== host) }); }
    else if (isBlocked) { btn.textContent = "Unblock"; btn.onclick = () => setPerms({ blocked: perms.blocked.filter((h) => h !== host) }); }
    else if (!decision.allow) { btn.textContent = "Allow here"; btn.onclick = () => setPerms({ allowed: [...perms.allowed, host] }); }
    else { btn.textContent = "Block here"; btn.onclick = () => setPerms({ blocked: [...perms.blocked, host] }); }
    row.append(label, btn); card.appendChild(row);
  }

  // Managed lists (blocked + allowed) with per-entry remove.
  const listSection = (title, key) => {
    const hosts = perms[key] || [];
    if (!hosts.length) return;
    const h = document.createElement("div"); h.textContent = title; h.style.cssText = "font-size:11px;opacity:.6;margin:8px 0 2px";
    card.appendChild(h);
    for (const hn of hosts) {
      const row = document.createElement("div"); row.className = "task-row";
      const txt = document.createElement("span"); txt.className = "tx"; txt.textContent = hn;
      const del = document.createElement("button"); del.className = "iconbtn"; del.title = "Remove"; del.textContent = "✕";
      del.onclick = () => setPerms({ [key]: hosts.filter((x) => x !== hn) });
      row.append(txt, del); card.appendChild(row);
    }
  };
  listSection("Blocked", "blocked");
  listSection("Allowed (overrides)", "allowed");

  // #4 default blocked categories toggle.
  const cat = document.createElement("label"); cat.className = "task-row"; cat.style.cssText = "cursor:pointer;margin-top:8px;gap:8px";
  const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = perms.blockCategories === true;
  cb.onchange = () => chrome.runtime.sendMessage({ type: "jarvis_site_perms_set", patch: { blockCategories: cb.checked } }).catch(() => {});
  const cl = document.createElement("span"); cl.className = "tx"; cl.textContent = "Block financial / adult / pirated sites by default";
  cat.append(cb, cl); card.appendChild(cat);

  stream.appendChild(card); stream.scrollTop = stream.scrollHeight;
}

// ── Workflow recording (feature 10) ────────────────────────────────────
let recording = false;
async function toggleRecord() {
  if (!recording) {
    const res = await chrome.runtime.sendMessage({ type: "jarvis_record_start" }).catch(() => ({ ok: false }));
    if (res && res.ok) { recording = true; note("● Recording — do the steps on the page, then reopen the menu and Stop."); }
    else note("Open a normal web page first, then record.");
    return;
  }
  const res = await chrome.runtime.sendMessage({ type: "jarvis_record_stop" }).catch(() => ({ ok: false }));
  recording = false;
  const steps = (res && res.steps) || [];
  if (!steps.length) { note("No steps were recorded."); return; }
  saveWorkflowForm(steps);
}
function saveWorkflowForm(steps) {
  $("emptyState")?.remove();
  const card = document.createElement("div"); card.className = "approval";
  const t = document.createElement("div"); t.className = "at"; t.textContent = `Recorded ${steps.length} step(s) — name this workflow:`;
  const name = document.createElement("input"); name.className = "task-sel"; name.style.marginTop = "0"; name.placeholder = "workflow name";
  const frow = document.createElement("div"); frow.className = "arow"; frow.style.marginTop = "9px";
  const cancel = document.createElement("button"); cancel.className = "btn"; cancel.textContent = "Discard"; cancel.style.cssText = "background:var(--panel);color:var(--fg)";
  const save = document.createElement("button"); save.className = "btn btn-primary"; save.textContent = "Save";
  cancel.addEventListener("click", () => card.remove());
  save.addEventListener("click", async () => {
    const n = name.value.trim(); if (!n) return;
    const { workflows } = await chrome.storage.local.get("workflows");
    const list = Array.isArray(workflows) ? workflows : [];
    await chrome.storage.local.set({ workflows: [...list.filter((w) => w.name !== n), { name: n, steps }] });
    card.remove(); addMsg(`✓ Saved workflow "${n}" (${steps.length} steps). Run it from the ⋮ menu → Workflows.`, "bot");
  });
  frow.append(cancel, save); card.append(t, name, frow);
  stream.appendChild(card); stream.scrollTop = stream.scrollHeight;
}
async function showWorkflows() {
  const { workflows } = await chrome.storage.local.get("workflows");
  const list = Array.isArray(workflows) ? workflows : [];
  $("emptyState")?.remove();
  const card = document.createElement("div"); card.className = "approval";
  const t = document.createElement("div"); t.className = "at"; t.textContent = list.length ? "Saved workflows" : "No workflows yet — use Record workflow.";
  card.appendChild(t);
  for (const wf of list) {
    const row = document.createElement("div"); row.className = "task-row";
    const txt = document.createElement("span"); txt.className = "tx"; txt.textContent = `${wf.name} · ${wf.steps.length} steps`;
    const run = document.createElement("button"); run.className = "iconbtn"; run.title = "Run"; run.textContent = "▶";
    const del = document.createElement("button"); del.className = "iconbtn"; del.title = "Delete"; del.textContent = "✕";
    run.addEventListener("click", async () => { addMsg(`Running workflow "${wf.name}"…`, "bot"); await chrome.runtime.sendMessage({ type: "jarvis_run_workflow", steps: wf.steps }).catch(() => {}); });
    del.addEventListener("click", async () => { const { workflows: w2 } = await chrome.storage.local.get("workflows"); await chrome.storage.local.set({ workflows: (w2 || []).filter((x) => x.name !== wf.name) }); row.remove(); });
    row.append(txt, run, del); card.appendChild(row);
  }
  stream.appendChild(card); stream.scrollTop = stream.scrollHeight;
}

// ── Boot ────────────────────────────────────────────────────────────────
(async () => {
  const { onboardingComplete, permissionMode } =
    await chrome.storage.local.get(["onboardingComplete", "permissionMode"]);
  if (permissionMode) setPerm(permissionMode);
  loadShortcuts();
  // ?view=<step> forces a specific screen (debugging / previews); normal opens
  // resolve to onboarding on first run, else the chat.
  const forced = new URLSearchParams(location.search).get("view");
  show(forced || (onboardingComplete ? "main" : "beta"));
})();

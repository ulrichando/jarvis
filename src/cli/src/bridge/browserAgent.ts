// browserAgent.ts — the "chat that acts" loop for Jarvis in Chrome.
//
// Runs a tool-use loop against the LLM proxy: the model reads the page and
// drives it through the extension's existing command surface (the same
// ext_browse actions the voice agent uses). Destructive steps are gated — in
// "ask" mode the extension's safety gate returns needs_confirmation and we ask
// the user before re-sending confirmed. Every step is streamed to the panel via
// onEvent so the transcript shows what Jarvis is doing.

export type AgentEvent =
  | { type: "text"; text: string }
  | { type: "action"; action: string; label: string }
  | { type: "action_result"; ok: boolean; summary: string }
  | { type: "approval"; approvalId: string; label: string };

export interface RunAgentOpts {
  text: string;
  model: string;
  proxyUrl: string;
  headers: Record<string, string>;
  mode: "ask" | "auto";
  sendCommand: (action: string, args: any, confirmed: boolean) => Promise<any>;
  requestApproval: (label: string) => Promise<boolean>;
  onEvent: (ev: AgentEvent) => void;
  maxSteps?: number;
}

// The browser surface exposed to the model. Mirrors the extension's HANDLERS +
// the background-dispatched tab actions; args match ext_browse.
const TOOLS = [
  { name: "get_url", description: "Get the current page URL and title.", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "dom_summary", description: "Structured summary of the page: headings, links, buttons, and form inputs (with selectors/labels). Call this first to see what's on the page.", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "extract_text", description: "Read the visible text of the page or of a specific element.", input_schema: { type: "object", properties: { selector: { type: "string", description: "optional CSS selector; omit for the whole page" } }, additionalProperties: false } },
  { name: "find_by_text", description: "Find clickable/interactive elements whose text contains the given string.", input_schema: { type: "object", properties: { text: { type: "string" } }, required: ["text"], additionalProperties: false } },
  { name: "read_console", description: "Read recent browser console messages (log/info/warn/error) from the page — for debugging.", input_schema: { type: "object", properties: { level: { type: "string", description: "optional filter: log|info|warn|error" } }, additionalProperties: false } },
  { name: "read_network", description: "List recent network requests the page made (URL + type + timing).", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "navigate", description: "Navigate the active tab to an http(s) URL.", input_schema: { type: "object", properties: { url: { type: "string" } }, required: ["url"], additionalProperties: false } },
  { name: "click", description: "Click the element matching a STANDARD CSS selector (id/class/attribute). Do NOT use Playwright pseudo-selectors like :has-text().", input_schema: { type: "object", properties: { selector: { type: "string" } }, required: ["selector"], additionalProperties: false } },
  { name: "click_text", description: "Click the button/link/control whose visible text matches — the reliable way to click by label without a selector.", input_schema: { type: "object", properties: { text: { type: "string" } }, required: ["text"], additionalProperties: false } },
  { name: "type", description: "Type text into the input/textarea matching a CSS selector (replaces its value).", input_schema: { type: "object", properties: { selector: { type: "string" }, text: { type: "string" } }, required: ["selector", "text"], additionalProperties: false } },
  { name: "fill_form", description: "Fill multiple fields at once. `fields` maps CSS selector OR field name/id → value.", input_schema: { type: "object", properties: { fields: { type: "object", additionalProperties: { type: "string" } } }, required: ["fields"], additionalProperties: false } },
  { name: "submit", description: "Submit a form (defaults to the first form; pass form_selector to target one).", input_schema: { type: "object", properties: { form_selector: { type: "string" } }, additionalProperties: false } },
  { name: "select", description: "Choose a value in a <select> dropdown.", input_schema: { type: "object", properties: { selector: { type: "string" }, value: { type: "string" } }, required: ["selector", "value"], additionalProperties: false } },
  { name: "scroll", description: "Scroll the page ('up'|'down'|'top'|'bottom') or scroll an element into view (selector).", input_schema: { type: "object", properties: { direction: { type: "string" }, selector: { type: "string" }, amount: { type: "number" } }, additionalProperties: false } },
  { name: "press_key", description: "Press a key (e.g. Enter, Tab, Escape) on an element or the active element.", input_schema: { type: "object", properties: { key: { type: "string" }, selector: { type: "string" } }, required: ["key"], additionalProperties: false } },
  { name: "wait_for", description: "Wait until an element matching the selector appears.", input_schema: { type: "object", properties: { selector: { type: "string" }, timeout_ms: { type: "number" } }, required: ["selector"], additionalProperties: false } },
  { name: "back", description: "Go back in history.", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "forward", description: "Go forward in history.", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "list_tabs", description: "List the user's open browser tabs (id, title, URL, and which tab group each belongs to). Use to find or reference another open tab, or to see the tabs in the current group.", input_schema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "activate_tab", description: "Switch to another open tab by its id (from list_tabs), then read/act on it — this is how you work across the tabs in a group.", input_schema: { type: "object", properties: { tab_id: { type: "number" } }, required: ["tab_id"], additionalProperties: false } },
  { name: "group_tabs", description: "Collect tabs into a visible, labeled 'Jarvis' tab group (a contained workspace). Pass tab_ids from list_tabs, or omit to group the current tab.", input_schema: { type: "object", properties: { tab_ids: { type: "array", items: { type: "number" } } }, additionalProperties: false } },
  { name: "download", description: "Download a file from a URL to the user's Downloads folder.", input_schema: { type: "object", properties: { url: { type: "string" }, filename: { type: "string" } }, required: ["url"], additionalProperties: false } },
];

const SYSTEM = `You are Jarvis, operating the user's web browser through tools to accomplish their request.
- When the task involves the current page, call dom_summary (or get_url) FIRST to see it before acting. If you can answer from your own knowledge without the page, just answer — don't call a tool.
- To click a button/link by its visible text, use click_text (do NOT invent CSS selectors like :has-text). Use click(selector) only with a precise standard CSS selector (id/class/attribute).
- Locate elements with dom_summary/find_by_text before acting.
- Working across tabs: list_tabs shows each tab's group; activate_tab switches to one so you can read/act on it; group_tabs collects the task's tabs into a labeled "Jarvis" group so the user can see your workspace. Prefer grouping when a task spans several tabs.
- After an action, re-check with dom_summary/extract_text before the next step.
- You know common sites — prefer their conventions over guessing: Gmail (compose = "c", the search box up top), Google Calendar (create = "c"), Google Docs (edit inline; menus under File/Edit/Insert), GitHub (a repo's Issues/Pull requests tabs; the "New issue" button), Slack (Ctrl/Cmd+K to jump to a channel/DM, the message box at the bottom).
- If you hit a login page, CAPTCHA, paywall, or 2FA prompt, STOP — do not attempt to sign in or solve it. Ask the user to handle that step, then continue when they say it's done.
- Destructive steps (delete, purchase, submit payment, enter credentials) may require user confirmation; if a tool result says it was denied, stop and explain.
- Keep going until the task is done, then reply with a SHORT plain-text summary and do NOT call another tool. If you only need to answer a question about the page, read it and answer.`;

function labelFor(action: string, args: any): string {
  const a = args || {};
  switch (action) {
    case "navigate": return `Navigate to ${a.url}`;
    case "click": return `Click ${a.selector}`;
    case "click_text": return `Click "${a.text}"`;
    case "type": return `Type into ${a.selector}`;
    case "fill_form": return `Fill ${Object.keys(a.fields || {}).length} field(s)`;
    case "submit": return `Submit form ${a.form_selector || a.selector || ""}`.trim();
    case "select": return `Select "${a.value}" in ${a.selector}`;
    case "scroll": return `Scroll ${a.direction || a.selector || "down"}`;
    case "press_key": return `Press ${a.key}`;
    case "wait_for": return `Wait for ${a.selector}`;
    case "dom_summary": return "Read the page";
    case "extract_text": return a.selector ? `Read ${a.selector}` : "Read the page text";
    case "find_by_text": return `Find "${a.text}"`;
    case "read_console": return "Read the console";
    case "read_network": return "Read network requests";
    case "get_url": return "Check the current URL";
    case "back": return "Go back";
    case "forward": return "Go forward";
    case "list_tabs": return "List open tabs";
    case "activate_tab": return `Switch to tab ${a.tab_id}`;
    case "group_tabs": return a.tab_ids && a.tab_ids.length ? `Group ${a.tab_ids.length} tabs` : "Group this tab";
    case "download": return `Download ${a.url}`;
    default: return action;
  }
}

// Cap the size of a tool result fed back to the model (dom_summary/extract_text
// can be large); keep it useful but bounded.
function summarizeResult(action: string, result: any): string {
  if (!result || result.ok === false) return `error: ${result?.error || "failed"}`;
  const s = JSON.stringify(result);
  return s.length > 4000 ? s.slice(0, 4000) + "…(truncated)" : s;
}

export async function runBrowserAgent(opts: RunAgentOpts): Promise<string> {
  const maxSteps = opts.maxSteps ?? 16;
  const messages: any[] = [{ role: "user", content: opts.text }];
  let finalText = "";

  for (let step = 0; step < maxSteps; step++) {
    let data: any;
    try {
      const resp = await fetch(`${opts.proxyUrl}/v1/messages`, {
        method: "POST",
        headers: opts.headers,
        body: JSON.stringify({
          model: opts.model,
          max_tokens: 1500,
          system: SYSTEM,
          messages,
          tools: TOOLS,
        }),
        signal: AbortSignal.timeout(45_000),
      });
      data = await resp.json();
    } catch (e: any) {
      return `Error reaching the model: ${e?.message || e}`;
    }
    if (data?.error) return `Error: ${data.error.message || JSON.stringify(data.error)}`;
    const content: any[] = Array.isArray(data?.content) ? data.content : [];

    const texts = content.filter((b) => b.type === "text").map((b) => b.text).filter(Boolean);
    if (texts.length) { finalText = texts.join("\n"); opts.onEvent({ type: "text", text: finalText }); }

    const toolUses = content.filter((b) => b.type === "tool_use");
    if (toolUses.length === 0) return finalText || "(done)";

    messages.push({ role: "assistant", content });
    const toolResults: any[] = [];
    for (const tu of toolUses) {
      const action = tu.name;
      const args = tu.input || {};
      // wait_for must resolve within the per-command timeout (15s in server.ts)
      // or the command aborts before the wait does — clamp under that budget.
      if (action === "wait_for" && typeof args.timeout_ms === "number") {
        args.timeout_ms = Math.min(Math.max(args.timeout_ms, 0), 12_000);
      }
      const label = labelFor(action, args);
      opts.onEvent({ type: "action", action, label });

      let result: any;
      try {
        // Background actions (download) bypass the content-script safety gate, so
        // gate the sensitive one here: download writes a file to disk, so ask
        // first in ask mode. Otherwise: auto mode confirms up front; ask mode
        // sends unconfirmed and lets the element-aware safety gate decide.
        const preConfirm = opts.mode === "ask" && action === "download";
        if (preConfirm && !(await opts.requestApproval(label))) {
          result = { ok: false, error: "denied by user" };
        } else {
          result = await opts.sendCommand(action, args, opts.mode === "auto" || preConfirm);
          if (opts.mode === "ask" && result && result.needs_confirmation) {
            const approved = await opts.requestApproval(label);
            result = approved
              ? await opts.sendCommand(action, args, true)
              : { ok: false, error: "denied by user" };
          }
        }
      } catch (e: any) {
        result = { ok: false, error: String(e?.message || e) };
      }
      opts.onEvent({ type: "action_result", ok: result?.ok !== false, summary: label });
      toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: summarizeResult(action, result) });
    }
    messages.push({ role: "user", content: toolResults });
  }
  return finalText || "I reached the step limit before finishing — want me to keep going?";
}

export const _AGENT_TOOLS = TOOLS; // for tests

// The browser surface exposed to a model, shared by the bridge's browserAgent
// (NL loop) and the CLI's jarvis-in-chrome MCP server. Mirrors the jarvis-screen
// extension's handlers 1:1; args match ext_browse.
export const TOOLS = [
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
] as const

export const SYSTEM = `You are Jarvis, operating the user's web browser through tools to accomplish their request.
- When the task involves the current page, call dom_summary (or get_url) FIRST to see it before acting. If you can answer from your own knowledge without the page, just answer — don't call a tool.
- To click a button/link by its visible text, use click_text (do NOT invent CSS selectors like :has-text). Use click(selector) only with a precise standard CSS selector (id/class/attribute).
- Locate elements with dom_summary/find_by_text before acting.
- Working across tabs: list_tabs shows each tab's group; activate_tab switches to one so you can read/act on it; group_tabs collects the task's tabs into a labeled "Jarvis" group so the user can see your workspace. Prefer grouping when a task spans several tabs.
- After an action, re-check with dom_summary/extract_text before the next step.
- You know common sites — prefer their conventions over guessing: Gmail (compose = "c", the search box up top), Google Calendar (create = "c"), Google Docs (edit inline; menus under File/Edit/Insert), GitHub (a repo's Issues/Pull requests tabs; the "New issue" button), Slack (Ctrl/Cmd+K to jump to a channel/DM, the message box at the bottom).
- If you hit a login page, CAPTCHA, paywall, or 2FA prompt, STOP — do not attempt to sign in or solve it. Ask the user to handle that step, then continue when they say it's done.
- Destructive steps (delete, purchase, submit payment, enter credentials) may require user confirmation; if a tool result says it was denied, stop and explain.
- Keep going until the task is done, then reply with a SHORT plain-text summary and do NOT call another tool. If you only need to answer a question about the page, read it and answer.`

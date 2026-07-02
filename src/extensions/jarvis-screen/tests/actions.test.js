/** @jest-environment jsdom */
const actions = require("../actions.js");

beforeEach(() => {
  document.body.innerHTML = `
    <h1>Welcome</h1>
    <a href="https://example.com/more" id="more">More information</a>
    <button id="b1">Click me</button>
    <input id="email" name="email" />
    <input id="password" name="password" type="password" />
    <select id="s1"><option value="a">A</option><option value="b">B</option></select>
    <form id="f1"><input name="q" /></form>
    <div id="deep" style="margin-top:2000px">bottom</div>
  `;
});

describe("reading", () => {
  test("get_url returns location + title", () => {
    const r = actions.ext_get_url();
    expect(r.ok).toBe(true);
    expect(typeof r.url).toBe("string");
    expect("title" in r).toBe(true);
  });
  test("extract_text default body", () => {
    const r = actions.ext_extract_text({});
    expect(r.ok).toBe(true);
    expect(r.text).toContain("Welcome");
  });
  test("extract_text invalid selector fails cleanly", () => {
    expect(actions.ext_extract_text({ selector: "###bad" }).ok).toBe(false);
  });
  test("find_by_text finds a match", () => {
    const r = actions.ext_find_by_text({ text: "More information" });
    expect(r.ok).toBe(true);
    expect(r.found).toBe(true);
  });
  test("find_by_text reports no match", () => {
    expect(actions.ext_find_by_text({ text: "nonexistent_xyz" }).found).toBe(false);
  });
  test("dom_summary returns headings + buttons + inputs", () => {
    const r = actions.ext_dom_summary();
    expect(r.ok).toBe(true);
    expect(r.headings).toContain("Welcome");
    expect(r.buttons.length).toBeGreaterThan(0);
    expect(r.inputs.length).toBeGreaterThan(0);
  });
});

describe("mouse + input", () => {
  test("click hits the element", () => {
    let clicked = false;
    document.getElementById("b1").addEventListener("click", () => { clicked = true; });
    expect(actions.ext_click({ selector: "#b1" }).ok).toBe(true);
    expect(clicked).toBe(true);
  });
  test("click on missing selector fails", () => {
    expect(actions.ext_click({ selector: "#nope" }).ok).toBe(false);
  });
  test("select sets dropdown value + fires change", () => {
    let changed = false;
    document.getElementById("s1").addEventListener("change", () => { changed = true; });
    expect(actions.ext_select({ selector: "#s1", value: "b" }).ok).toBe(true);
    expect(document.getElementById("s1").value).toBe("b");
    expect(changed).toBe(true);
  });
  test("type fills the input + fires input event", () => {
    let fired = false;
    document.getElementById("email").addEventListener("input", () => { fired = true; });
    expect(actions.ext_type({ selector: "#email", text: "a@b.com" }).ok).toBe(true);
    expect(document.getElementById("email").value).toBe("a@b.com");
    expect(fired).toBe(true);
  });
  test("fill_form fills by name + reports missing", () => {
    const r = actions.ext_fill_form({ fields: { q: "hello", nope: "x" } });
    expect(document.querySelector('[name="q"]').value).toBe("hello");
    expect(r.ok).toBe(false); // one field missing
    expect(r.filled).toContain("q");
  });
  test("press_key dispatches keydown", () => {
    let key = null;
    document.getElementById("email").addEventListener("keydown", (e) => { key = e.key; });
    expect(actions.ext_press_key({ selector: "#email", key: "Enter" }).ok).toBe(true);
    expect(key).toBe("Enter");
  });
});

describe("scroll + wait + close", () => {
  test("scroll returns ok", () => {
    expect(actions.ext_scroll({ direction: "down" }).ok).toBe(true);
  });
  test("wait_for resolves when the element exists", async () => {
    const r = await actions.ext_wait_for({ selector: "#b1", timeout_ms: 500 });
    expect(r.ok).toBe(true);
  });
  test("wait_for times out for a missing element", async () => {
    const r = await actions.ext_wait_for({ selector: "#never", timeout_ms: 200 });
    expect(r.ok).toBe(false);
  });
  test("close_tab returns a close request", () => {
    expect(actions.ext_close_tab()).toEqual({ ok: true, action: "close_requested" });
  });
});

describe("handler map", () => {
  test("HANDLERS covers the content-side actions and excludes exec_js", () => {
    const keys = Object.keys(actions.HANDLERS);
    for (const a of ["get_url", "extract_text", "dom_summary", "click", "type", "fill_form", "submit", "select", "scroll", "wait_for", "close_tab"]) {
      expect(keys).toContain(a);
    }
    expect(keys).not.toContain("exec_js"); // dropped in v1 (injection surface)
  });
});

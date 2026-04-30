const actions = require('../actions.js');

describe('navigation actions', () => {
  test('ext_get_url returns location info', () => {
    document.title = 'Test Page';
    Object.defineProperty(window, 'location', {
      value: { href: 'https://example.com/foo' },
      writable: true,
    });
    expect(actions.ext_get_url()).toEqual({
      ok: true, url: 'https://example.com/foo', title: 'Test Page'
    });
  });

  test('ext_close_tab returns ok (extension context handles actual close)', () => {
    expect(actions.ext_close_tab()).toEqual({ ok: true, action: 'close_requested' });
  });
});

describe('page reading actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <h1>Title</h1>
      <h2>Subhead</h2>
      <p>Hello world.</p>
      <button id="btn1" aria-label="Submit">Submit</button>
      <a href="/x" role="link">More information</a>
      <input type="text" name="email" placeholder="email@x.com">
    `;
  });

  test('ext_extract_text default body', () => {
    const r = actions.ext_extract_text({});
    expect(r.ok).toBe(true);
    expect(r.text).toContain('Hello world');
    expect(r.text).toContain('Title');
  });

  test('ext_extract_text by selector', () => {
    const r = actions.ext_extract_text({ selector: 'p' });
    expect(r.text).toBe('Hello world.');
  });

  test('ext_extract_text invalid selector', () => {
    const r = actions.ext_extract_text({ selector: '###bad' });
    expect(r.ok).toBe(false);
  });

  test('ext_find_by_text exact match', () => {
    const r = actions.ext_find_by_text({ text: 'More information' });
    expect(r.ok).toBe(true);
    expect(r.matches.length).toBeGreaterThan(0);
  });

  test('ext_find_by_text no match', () => {
    const r = actions.ext_find_by_text({ text: 'nonexistent_xyz' });
    expect(r.ok).toBe(true);
    expect(r.matches).toEqual([]);
  });

  test('ext_dom_summary returns headings + actionable elements', () => {
    const r = actions.ext_dom_summary();
    expect(r.ok).toBe(true);
    expect(r.headings.find(h => h.text === 'Title')).toBeDefined();
    expect(r.actionable_elements.length).toBeGreaterThan(0);
    const btn = r.actionable_elements.find(e => e.role === 'button' || e.tag === 'button');
    expect(btn).toBeDefined();
  });

  test('ext_screenshot returns placeholder ok (real screenshot in background.js)', () => {
    const r = actions.ext_screenshot();
    expect(r.ok).toBe(true);
    expect(r.delegated_to_background).toBe(true);
  });
});

describe('mouse actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="b1">B1</button>
      <select id="s1">
        <option value="a">A</option>
        <option value="b">B</option>
      </select>
      <div id="src" draggable="true">Source</div>
      <div id="tgt">Target</div>
    `;
  });

  test('ext_click hits the element', () => {
    let clicked = false;
    document.getElementById('b1').addEventListener('click', () => { clicked = true; });
    const r = actions.ext_click({ selector: '#b1' });
    expect(r.ok).toBe(true);
    expect(clicked).toBe(true);
  });

  test('ext_click selector not found', () => {
    expect(actions.ext_click({ selector: '#nope' }).ok).toBe(false);
  });

  test('ext_right_click fires contextmenu', () => {
    let fired = false;
    document.getElementById('b1').addEventListener('contextmenu', () => { fired = true; });
    expect(actions.ext_right_click({ selector: '#b1' }).ok).toBe(true);
    expect(fired).toBe(true);
  });

  test('ext_hover fires mouseover', () => {
    let fired = false;
    document.getElementById('b1').addEventListener('mouseover', () => { fired = true; });
    expect(actions.ext_hover({ selector: '#b1' }).ok).toBe(true);
    expect(fired).toBe(true);
  });

  test('ext_select sets dropdown value', () => {
    expect(actions.ext_select({ selector: '#s1', value: 'b' }).ok).toBe(true);
    expect(document.getElementById('s1').value).toBe('b');
  });

  test('ext_drag fires dragstart and drop', () => {
    const events = [];
    ['dragstart','dragend','drop'].forEach(ev =>
      document.getElementById(ev === 'drop' ? 'tgt' : 'src')
        .addEventListener(ev, () => events.push(ev)));
    expect(actions.ext_drag({ from_selector: '#src', to_selector: '#tgt' }).ok).toBe(true);
    expect(events).toContain('dragstart');
  });
});

describe('keyboard / input actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input type="text" id="email" name="email" placeholder="email">
      <input type="text" id="name" name="name" placeholder="name">
      <textarea id="msg"></textarea>
      <form id="f1">
        <input type="text" name="q" id="q">
      </form>
    `;
  });

  test('ext_type fills the input', () => {
    expect(actions.ext_type({selector: '#email', text: 'a@b.com'}).ok).toBe(true);
    expect(document.getElementById('email').value).toBe('a@b.com');
  });

  test('ext_type fires input event', () => {
    let fired = false;
    document.getElementById('email').addEventListener('input', () => { fired = true; });
    actions.ext_type({selector: '#email', text: 'x'});
    expect(fired).toBe(true);
  });

  test('ext_fill_form by name', () => {
    const r = actions.ext_fill_form({fields: { email: 'a@b.com', name: 'Bob' }});
    expect(r.ok).toBe(true);
    expect(r.filled_count).toBe(2);
    expect(document.getElementById('email').value).toBe('a@b.com');
    expect(document.getElementById('name').value).toBe('Bob');
  });

  test('ext_fill_form reports missing fields', () => {
    const r = actions.ext_fill_form({fields: { unknownX: 'v' }});
    expect(r.ok).toBe(true);
    expect(r.missing).toEqual(['unknownX']);
  });

  test('ext_keypress dispatches keydown+keyup', () => {
    const events = [];
    document.addEventListener('keydown', e => events.push(['down', e.key]));
    document.addEventListener('keyup',   e => events.push(['up',   e.key]));
    actions.ext_keypress({key: 'Enter'});
    expect(events).toEqual([['down', 'Enter'], ['up', 'Enter']]);
  });

  test('ext_submit submits the form', () => {
    let submitted = false;
    document.getElementById('f1').addEventListener('submit', e => {
      e.preventDefault(); submitted = true;
    });
    expect(actions.ext_submit({form_selector: '#f1'}).ok).toBe(true);
    expect(submitted).toBe(true);
  });
});

describe('scroll/wait/dialog actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="late" style="display:none">late</div>`;
    Object.defineProperty(window, 'scrollTo', {
      value: jest.fn((x, y) => { window._sx = x; window._sy = y; }),
      writable: true,
    });
    window.scrollX = 0;
    window.scrollY = 0;
  });

  test('ext_scroll down', () => {
    expect(actions.ext_scroll({direction: 'down', amount: 500}).ok).toBe(true);
    expect(window.scrollTo).toHaveBeenCalledWith(0, 500);
  });

  test('ext_scroll up after down', () => {
    actions.ext_scroll({direction: 'down', amount: 1000});
    actions.ext_scroll({direction: 'up', amount: 300});
    expect(window.scrollTo).toHaveBeenLastCalledWith(0, -300);
  });

  test('ext_scroll page', () => {
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    actions.ext_scroll({direction: 'down', amount: 'page'});
    expect(window.scrollTo).toHaveBeenCalledWith(0, 800);
  });

  test('ext_wait_for finds element that already exists', async () => {
    document.getElementById('late').style.display = 'block';
    const r = await actions.ext_wait_for({selector: '#late', timeout: 1});
    expect(r.found).toBe(true);
  });

  test('ext_wait_for times out for missing element', async () => {
    const r = await actions.ext_wait_for({selector: '#never', timeout: 0.2});
    expect(r.found).toBe(false);
  });

  test('ext_accept_dialog returns ok (delegated to background)', () => {
    expect(actions.ext_accept_dialog({accept: true}).ok).toBe(true);
  });

  test('ext_switch_iframe returns ok or error', () => {
    expect(actions.ext_switch_iframe({selector_or_index: 0}).ok).toBe(false);
  });
});

describe('power tools', () => {
  test('ext_exec_js runs the code and returns result', () => {
    const r = actions.ext_exec_js({code: '1 + 2'});
    expect(r.ok).toBe(true);
    expect(r.result).toBe(3);
  });
  test('ext_exec_js returns error on bad code', () => {
    const r = actions.ext_exec_js({code: 'this.does.not.exist'});
    expect(r.ok).toBe(false);
  });
  test('ext_get_cookies delegated to background', () => {
    const r = actions.ext_get_cookies({domain: 'example.com'});
    expect(r.delegated_to_background).toBe(true);
  });
  test('ext_set_cookies delegated to background', () => {
    const r = actions.ext_set_cookies({domain: 'example.com', cookies: []});
    expect(r.delegated_to_background).toBe(true);
  });
});

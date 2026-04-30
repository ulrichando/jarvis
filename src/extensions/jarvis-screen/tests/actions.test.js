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

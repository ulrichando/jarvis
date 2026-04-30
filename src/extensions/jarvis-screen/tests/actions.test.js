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

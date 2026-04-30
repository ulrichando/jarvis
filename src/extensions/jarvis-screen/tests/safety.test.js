// tests/safety.test.js
const { isDestructive, hasCredentials, isAllowedDomain } = require('../safety.js');

describe('isDestructive', () => {
  test.each([
    [{action: 'click', args: {selector: 'button.delete'}}, true],
    [{action: 'click', args: {selector: 'button#purchase'}}, true],
    [{action: 'click', args: {selector: 'a.cancel-subscription'}}, true],
    [{action: 'submit', args: {form_selector: 'form#payment'}}, true],
    [{action: 'click', args: {selector: 'button.subscribe'}}, false],
    [{action: 'extract_text', args: {selector: 'body'}}, false],
    [{action: 'navigate', args: {url: 'https://example.com'}}, false],
    [{action: 'exec_js', args: {code: 'document.title'}}, true],          // always-confirm
    [{action: 'set_cookies', args: {domain: 'example.com'}}, true],       // always-confirm
  ])('isDestructive(%j) === %s', (cmd, expected) => {
    expect(isDestructive(cmd)).toBe(expected);
  });
});

describe('hasCredentials', () => {
  test.each([
    [{action: 'type', args: {selector: '#pw', text: 'mypassword'}}, true],
    [{action: 'fill_form', args: {fields: {otp: '123456'}}}, true],
    [{action: 'fill_form', args: {fields: {cvv: '123'}}}, true],
    [{action: 'type', args: {selector: '#email', text: 'a@b.com'}}, false],
  ])('hasCredentials(%j) === %s', (cmd, want) => {
    expect(hasCredentials(cmd)).toBe(want);
  });
});

describe('isAllowedDomain', () => {
  test('empty allowlist permits all', () => {
    expect(isAllowedDomain('https://example.com', [])).toBe(true);
    expect(isAllowedDomain('https://example.com', null)).toBe(true);
  });
  test('exact match allowed', () => {
    expect(isAllowedDomain('https://gmail.com/inbox', ['gmail.com'])).toBe(true);
  });
  test('subdomain allowed', () => {
    expect(isAllowedDomain('https://mail.gmail.com', ['gmail.com'])).toBe(true);
  });
  test('non-listed blocked', () => {
    expect(isAllowedDomain('https://evil.com', ['gmail.com'])).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';

import {
  convertCookieInputToPlaywrightCookies,
  parseCookieHeaderPairs,
} from './rpaMcpTest';

describe('parseCookieHeaderPairs', () => {
  it('accepts Cookie header lines with prefix', () => {
    expect(parseCookieHeaderPairs('Cookie: sid=abc; theme=dark')).toEqual([
      { name: 'sid', value: 'abc' },
      { name: 'theme', value: 'dark' },
    ]);
  });

  it('accepts raw cookie header values', () => {
    expect(parseCookieHeaderPairs('sid=abc; theme=dark')).toEqual([
      { name: 'sid', value: 'abc' },
      { name: 'theme', value: 'dark' },
    ]);
  });
});

describe('convertCookieInputToPlaywrightCookies', () => {
  it('converts cookie header input into playwright cookies', () => {
    expect(convertCookieInputToPlaywrightCookies({
      mode: 'cookie_header',
      text: 'Cookie: sid=abc; theme=dark',
      domain: 'example.com',
      required: true,
    })).toEqual([
      { name: 'sid', value: 'abc', domain: '.example.com', path: '/' },
      { name: 'theme', value: 'dark', domain: '.example.com', path: '/' },
    ]);
  });

  it('passes through playwright cookie arrays', () => {
    expect(convertCookieInputToPlaywrightCookies({
      mode: 'playwright_json',
      text: JSON.stringify([{ name: 'sid', value: 'abc', domain: '.example.com', path: '/' }]),
      required: true,
    })).toEqual([
      { name: 'sid', value: 'abc', domain: '.example.com', path: '/' },
    ]);
  });

  it('returns undefined for optional empty input', () => {
    expect(convertCookieInputToPlaywrightCookies({
      mode: 'header_value',
      text: '   ',
      domain: 'example.com',
      required: false,
    })).toBeUndefined();
  });

  it('requires a domain when converting header input', () => {
    expect(() => convertCookieInputToPlaywrightCookies({
      mode: 'header_value',
      text: 'sid=abc',
      required: true,
    })).toThrow('Cookie domain is required');
  });
});

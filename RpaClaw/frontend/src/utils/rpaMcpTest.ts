export type CookieInputMode = 'cookie_header' | 'header_value' | 'playwright_json';

export interface PlaywrightCookieLike {
  name: string;
  value: string;
  domain?: string;
  path?: string;
  url?: string;
  [key: string]: unknown;
}

export interface CookieNameValuePair {
  name: string;
  value: string;
}

export interface CookieConversionInput {
  mode: CookieInputMode;
  text: string;
  domain?: string;
  required: boolean;
}

function normalizeCookieHeaderText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return '';
  return trimmed.replace(/^Cookie\s*:\s*/i, '').trim();
}

export function parseCookieHeaderPairs(text: string): CookieNameValuePair[] {
  const normalized = normalizeCookieHeaderText(text);
  if (!normalized) return [];

  return normalized
    .split(';')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => {
      const separatorIndex = segment.indexOf('=');
      if (separatorIndex <= 0) {
        throw new Error(`Invalid cookie segment: ${segment}`);
      }
      const name = segment.slice(0, separatorIndex).trim();
      const value = segment.slice(separatorIndex + 1).trim();
      if (!name) {
        throw new Error(`Invalid cookie segment: ${segment}`);
      }
      return { name, value };
    });
}

function normalizeCookieDomain(domain?: string): string {
  const normalized = (domain || '').trim().replace(/^\.+/, '');
  if (!normalized) {
    throw new Error('Cookie domain is required');
  }
  return `.${normalized}`;
}

function parsePlaywrightCookieArray(text: string): PlaywrightCookieLike[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('Cookies JSON is invalid');
  }
  if (!Array.isArray(parsed)) {
    throw new Error('Cookies JSON must be an array');
  }
  for (const item of parsed) {
    if (!item || typeof item !== 'object') {
      throw new Error('Each cookie must be an object');
    }
  }
  return parsed as PlaywrightCookieLike[];
}

export function convertCookieInputToPlaywrightCookies(input: CookieConversionInput): PlaywrightCookieLike[] | undefined {
  const text = input.text.trim();
  if (!text) {
    if (input.required) {
      throw new Error('Cookie input is required');
    }
    return undefined;
  }

  if (input.mode === 'playwright_json') {
    return parsePlaywrightCookieArray(text);
  }

  const domain = normalizeCookieDomain(input.domain);
  return parseCookieHeaderPairs(text).map(({ name, value }) => ({
    name,
    value,
    domain,
    path: '/',
  }));
}

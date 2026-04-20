import { describe, expect, it } from 'vitest';

import {
  buildRecordedStepSummary,
  buildSchemaSummary,
  countSchemaProperties,
  shouldShowCookieSection,
} from './rpaMcpEditorView';

describe('countSchemaProperties', () => {
  it('counts top-level schema properties', () => {
    expect(countSchemaProperties({
      type: 'object',
      properties: {
        repo: { type: 'string' },
        title: { type: 'string' },
      },
    })).toBe(2);
  });

  it('returns zero for missing or invalid property maps', () => {
    expect(countSchemaProperties(null)).toBe(0);
    expect(countSchemaProperties({ type: 'object' })).toBe(0);
    expect(countSchemaProperties({ properties: [] })).toBe(0);
  });
});

describe('buildRecordedStepSummary', () => {
  it('summarizes validation and locator coverage from recorded steps', () => {
    expect(buildRecordedStepSummary([
      { validation: { status: 'ok' }, locator_candidates: [{ selected: true }] },
      { validation: { status: 'warning' }, locator_candidates: [{ selected: true }, {}] },
      { validation: { status: 'broken' } },
      {},
    ])).toEqual({
      total: 4,
      strict: 1,
      needsAttention: 2,
      withCandidates: 2,
    });
  });
});

describe('buildSchemaSummary', () => {
  it('reports input and output field counts', () => {
    expect(buildSchemaSummary({
      input_schema: { properties: { repo: {}, title: {} } },
      output_schema: { properties: { success: {}, data: {}, downloads: {} } },
    })).toEqual({
      inputFields: 2,
      outputFields: 3,
    });
  });
});

describe('shouldShowCookieSection', () => {
  it('does not show the cookie editor while preview is unavailable', () => {
    expect(shouldShowCookieSection(null, true)).toBe(false);
  });

  it('shows the cookie editor for required cookies or explicit expansion', () => {
    expect(shouldShowCookieSection({ requires_cookies: true }, false)).toBe(true);
    expect(shouldShowCookieSection({ requires_cookies: false }, true)).toBe(true);
    expect(shouldShowCookieSection({ requires_cookies: false }, false)).toBe(false);
  });
});

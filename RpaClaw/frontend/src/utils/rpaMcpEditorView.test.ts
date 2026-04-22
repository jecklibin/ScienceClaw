import { describe, expect, it } from 'vitest';

import {
  buildRecordingStepsFromMcpEditorSteps,
  buildRecordedStepSummary,
  buildSchemaSummary,
  buildWorkflowInputsFromSchema,
  buildWorkflowOutputsFromSchema,
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

describe('buildWorkflowInputsFromSchema', () => {
  it('converts MCP input schema to recording workflow inputs and skips cookies', () => {
    expect(buildWorkflowInputsFromSchema({
      type: 'object',
      required: ['query'],
      properties: {
        query: { type: 'string', description: 'Search query', default: 'rpa' },
        page: { type: 'integer', description: 'Page number' },
        cookies: { type: 'array' },
      },
    })).toEqual([
      {
        name: 'query',
        type: 'string',
        required: true,
        source: 'user',
        description: 'Search query',
        default: 'rpa',
      },
      {
        name: 'page',
        type: 'number',
        required: false,
        source: 'user',
        description: 'Page number',
        default: undefined,
      },
    ]);
  });
});

describe('buildWorkflowOutputsFromSchema', () => {
  it('prefers nested data properties from MCP output schema', () => {
    expect(buildWorkflowOutputsFromSchema({
      type: 'object',
      properties: {
        success: { type: 'boolean' },
        data: {
          type: 'object',
          required: ['project_name'],
          properties: {
            project_name: { type: 'string', description: 'First project name' },
          },
        },
      },
    })).toEqual([
      {
        name: 'project_name',
        type: 'string',
        required: true,
        description: 'First project name',
        source: 'segment_output',
      },
    ]);
  });
});

describe('buildRecordingStepsFromMcpEditorSteps', () => {
  it('normalizes editor steps for conversational segment completion', () => {
    expect(buildRecordingStepsFromMcpEditorSteps([
      {
        id: 'step-1',
        action: 'click',
        description: 'Click button',
        target: { method: 'role', role: 'button', name: 'Save' },
        validation: { status: 'ok' },
      },
    ])).toMatchObject([
      {
        id: 'step-1',
        step_index: 0,
        action: 'click',
        description: 'Click button',
        target: '{"method":"role","role":"button","name":"Save"}',
        validation: { status: 'ok' },
      },
    ]);
  });
});

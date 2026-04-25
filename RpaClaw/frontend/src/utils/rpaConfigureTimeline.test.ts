import { describe, expect, it } from 'vitest';
import {
  getLegacyRpaSteps,
  getManualRecordingDiagnostics,
  hasManualRecordingDiagnostics,
  mapRpaConfigureDisplaySteps,
} from './rpaConfigureTimeline';

describe('rpaConfigureTimeline', () => {
  it('prefers recorded actions over traces and legacy steps when present', () => {
    const session = {
      steps: [
        {
          id: 'legacy-1',
          action: 'click',
          description: 'legacy click should only remain for parameterization',
        },
      ],
      traces: [
        {
          trace_id: 'trace-manual',
          trace_type: 'manual_action',
          source: 'record',
          action: 'click',
          description: 'legacy manual trace',
        },
      ],
      recorded_actions: [
        {
          step_id: 'step-search',
          action_kind: 'click',
          description: '点击 button("Search")',
          target: { method: 'role', role: 'button', name: 'Search' },
          validation: { status: 'ok' },
          page_state: { url: 'https://example.test/search' },
        },
      ],
    };

    const displaySteps = mapRpaConfigureDisplaySteps(session);

    expect(displaySteps).toHaveLength(1);
    expect(displaySteps[0]).toMatchObject({
      id: 'step-search',
      action: 'click',
      description: '点击 button("Search")',
      source: 'record',
      url: 'https://example.test/search',
      validation: { status: 'ok', details: 'Accepted manual action' },
    });
    expect(displaySteps[0].target).toEqual({ method: 'role', role: 'button', name: 'Search' });
  });

  it('keeps AI traces when recorded actions replace manual traces', () => {
    const session = {
      steps: [
        {
          id: 'step-search',
          action: 'click',
          description: 'legacy click should only remain for parameterization',
        },
      ],
      traces: [
        {
          trace_id: 'trace-step-search',
          trace_type: 'manual_action',
          source: 'manual',
          action: 'click',
          description: 'legacy manual trace',
        },
        {
          trace_id: 'trace-ai-select',
          trace_type: 'ai_operation',
          source: 'ai',
          user_instruction: 'click the first project',
          description: 'Click first project',
          after_page: { url: 'https://github.com/example/repo' },
          ai_execution: { code: 'async def run(page, results):\n    return {}' },
        },
      ],
      recorded_actions: [
        {
          step_id: 'step-search',
          action_kind: 'click',
          description: 'click button("Search")',
          target: { method: 'role', role: 'button', name: 'Search' },
          validation: { status: 'ok' },
          page_state: { url: 'https://example.test/search' },
        },
      ],
    };

    const displaySteps = mapRpaConfigureDisplaySteps(session);

    expect(displaySteps.map((step) => step.description)).toEqual([
      'click button("Search")',
      'Click first project',
    ]);
    expect(displaySteps.map((step) => step.source)).toEqual(['record', 'ai']);
    expect(displaySteps[1]).toMatchObject({
      id: 'trace-ai-select',
      action: 'ai_operation',
      url: 'https://github.com/example/repo',
      validation: { status: 'ok', details: 'AI Trace' },
    });
  });

  it('keeps accepted traces as fallback when recorded actions are absent', () => {
    const session = {
      steps: [
        { id: 'fill-1', action: 'fill', value: 'Alice', sensitive: false },
      ],
      traces: [
        {
          trace_id: 'trace-fill',
          trace_type: 'dataflow_fill',
          description: 'Dataflow fill',
        },
      ],
    };

    expect(mapRpaConfigureDisplaySteps(session)).toHaveLength(1);
    expect(getLegacyRpaSteps(session)).toEqual(session.steps);
  });

  it('falls back to legacy steps when no recorded actions or traces are present', () => {
    const session = {
      steps: [
        { id: 'click-1', action: 'click', description: 'Click search' },
      ],
      traces: [],
      recorded_actions: [],
    };

    expect(mapRpaConfigureDisplaySteps(session)).toEqual(session.steps);
  });

  it('maps recording diagnostics back to legacy step indexes', () => {
    const session = {
      steps: [
        {
          id: 'step-bad',
          action: 'fill',
          description: '输入 "foo" 到 None',
          locator_candidates: [{ playwright_locator: 'page.locator(".mystery")', selected: true }],
          url: 'https://example.test/search',
        },
      ],
      recording_diagnostics: [
        {
          related_step_id: 'step-bad',
          related_action_kind: 'fill',
          failure_reason: 'canonical_target_missing',
          raw_candidates: [{ playwright_locator: 'page.locator(".mystery")', selected: true }],
          page_state: { url: 'https://example.test/search' },
        },
      ],
    };

    const diagnostics = getManualRecordingDiagnostics(session);

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      stepId: 'step-bad',
      stepIndex: 0,
      action: 'fill',
      failureReason: 'canonical_target_missing',
      validation: { status: 'broken', details: 'canonical target missing' },
      configurable: true,
      url: 'https://example.test/search',
    });
    expect(hasManualRecordingDiagnostics(session)).toBe(true);
  });
});

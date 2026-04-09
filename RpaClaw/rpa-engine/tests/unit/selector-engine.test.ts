import { describe, expect, it } from 'vitest';
import { buildSelectorRecord } from '../../src/playwright/selector-engine.js';

describe('buildSelectorRecord', () => {
  it('keeps the primary selector and alternatives in order', () => {
    const selector = buildSelectorRecord(
      {
        selector: 'internal:role=button[name="Save"]',
        locatorAst: { kind: 'role', name: 'Save' },
      },
      [
        {
          selector: 'internal:testid=[data-testid="save-button"]',
          locatorAst: { kind: 'testId', value: 'save-button' },
          score: 100,
          matchCount: 1,
          visibleMatchCount: 1,
          isSelected: false,
          engine: 'playwright',
          reason: 'fallback',
        },
      ],
    );

    expect(selector.locator.selector).toBe('internal:role=button[name="Save"]');
    expect(selector.locatorAlternatives).toHaveLength(1);
    expect(selector.locatorAlternatives[0]?.selector).toBe(
      'internal:testid=[data-testid="save-button"]',
    );
  });
});

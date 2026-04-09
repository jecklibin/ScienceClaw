import { describe, expect, it } from 'vitest';
import { normalizeAction } from '../../src/action-model.js';

describe('normalizeAction', () => {
  it('keeps click as the action kind and moves popup into signals', () => {
    const action = normalizeAction({
      kind: 'click',
      pageAlias: 'page',
      framePath: [],
      locator: {
        selector: 'internal:role=button[name="Open"]',
        locatorAst: {},
      },
      signals: { popup: { targetPageAlias: 'popup1' } },
    });

    expect(action.kind).toBe('click');
    expect(action.signals.popup).toEqual({ targetPageAlias: 'popup1' });
    expect(action.status).toBe('recorded');
  });
});

import { describe, expect, it } from 'vitest';
import { getSandboxPreviewTabs } from './sandboxPreviewTabs';

describe('getSandboxPreviewTabs', () => {
  it('shows only Terminal in the activity panel terminal preview', () => {
    expect(getSandboxPreviewTabs('panel', 'terminal')).toEqual([
      { id: 'terminal', label: 'Terminal' },
    ]);
  });

  it('shows only Browser in the inline chat preview', () => {
    expect(getSandboxPreviewTabs('inline', 'browser')).toEqual([
      { id: 'browser', label: 'Browser' },
    ]);
  });
});

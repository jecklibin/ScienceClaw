import { describe, expect, it } from 'vitest';
import { getBrowserPreviewTabLabel, resolveActiveBrowserTabId } from './browserPreviewTabs';

describe('browserPreviewTabs', () => {
  it('uses the active tab from a tabs snapshot', () => {
    expect(resolveActiveBrowserTabId([
      { tab_id: 'tab-1', title: 'First', url: 'https://a.test', status: 'open', active: false },
      { tab_id: 'tab-2', title: 'Second', url: 'https://b.test', status: 'open', active: true },
    ], 'tab-1')).toBe('tab-2');
  });

  it('preserves the previous active tab if the snapshot has no explicit active tab', () => {
    expect(resolveActiveBrowserTabId([
      { tab_id: 'tab-1', title: 'First', url: 'https://a.test', status: 'open', active: false },
      { tab_id: 'tab-2', title: 'Second', url: 'https://b.test', status: 'open', active: false },
    ], 'tab-2')).toBe('tab-2');
  });

  it('falls back to url and then New Tab for tab labels', () => {
    expect(getBrowserPreviewTabLabel({ tab_id: 'tab-1', title: '', url: 'https://a.test', status: 'open', active: false })).toBe('https://a.test');
    expect(getBrowserPreviewTabLabel({ tab_id: 'tab-2', title: '', url: '', status: 'open', active: false })).toBe('New Tab');
  });
});

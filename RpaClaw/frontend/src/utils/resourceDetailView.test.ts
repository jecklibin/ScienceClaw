import { describe, expect, it } from 'vitest';

import { buildResourceSummaryCounts } from './resourceDetailView';

describe('buildResourceSummaryCounts', () => {
  it('counts params, steps, and files for overview cards', () => {
    expect(buildResourceSummaryCounts({
      params: { query: {}, page: {} },
      steps: [{ id: '1' }, { id: '2' }, { id: '3' }],
      files: [{ path: 'SKILL.md' }, { path: 'skill.py' }],
    })).toEqual({ params: 2, steps: 3, files: 2 });
  });
});

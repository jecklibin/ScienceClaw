import { describe, expect, it } from 'vitest';

import { canUseRecordedSkillOverview } from './skillDetailMode';

describe('canUseRecordedSkillOverview', () => {
  it('requires skill.meta.json and rpa-recording overview mode', () => {
    expect(canUseRecordedSkillOverview({
      files: [{ name: 'skill.meta.json', path: 'skill.meta.json', type: 'file' }],
      detail: { can_use_overview: true, mode: 'recorded-overview' },
    })).toBe(true);

    expect(canUseRecordedSkillOverview({
      files: [{ name: 'SKILL.md', path: 'SKILL.md', type: 'file' }],
      detail: { can_use_overview: false, mode: 'files' },
    })).toBe(false);
  });
});

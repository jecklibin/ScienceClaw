import { describe, expect, it } from 'vitest';
import { pickDefaultSkillFile } from './skillDetailView';

describe('pickDefaultSkillFile', () => {
  it('selects SKILL.md from the root file list by default', () => {
    expect(pickDefaultSkillFile([
      { name: 'params.json', path: 'params.json', type: 'file' },
      { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
    ])).toEqual({ name: 'SKILL.md', path: 'SKILL.md', type: 'file' });
  });

  it('does not select a fallback file when SKILL.md is missing', () => {
    expect(pickDefaultSkillFile([
      { name: 'params.json', path: 'params.json', type: 'file' },
      { name: 'skill.py', path: 'skill.py', type: 'file' },
    ])).toBeNull();
  });

  it('ignores SKILL.md directories', () => {
    expect(pickDefaultSkillFile([
      { name: 'SKILL.md', path: 'SKILL.md', type: 'directory' },
    ])).toBeNull();
  });
});

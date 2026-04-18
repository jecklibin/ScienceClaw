export interface SkillFileItem {
  name: string;
  path: string;
  type: string;
}

export function pickDefaultSkillFile(items: SkillFileItem[]): SkillFileItem | null {
  return items.find((item) => item.type === 'file' && item.name === 'SKILL.md') ?? null;
}

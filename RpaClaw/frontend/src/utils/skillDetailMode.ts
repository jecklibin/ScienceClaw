type SkillFileLike = {
  name: string;
  path: string;
  type: string;
};

type SkillDetailLike = {
  can_use_overview?: boolean;
  mode?: string;
} | null;

export function canUseRecordedSkillOverview(input: {
  files: SkillFileLike[];
  detail: SkillDetailLike;
}) {
  const hasMeta = input.files.some((file) => file.type === 'file' && file.name === 'skill.meta.json');
  return hasMeta && input.detail?.can_use_overview === true && input.detail?.mode === 'recorded-overview';
}

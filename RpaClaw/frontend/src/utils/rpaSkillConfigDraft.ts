export interface RpaDraftParam {
  original_value: string;
  default_value?: string;
  enabled?: boolean;
  sensitive?: boolean;
  credential_id?: string;
  type?: string;
  description?: string;
  required?: boolean;
}

export interface RpaSkillConfigDraft {
  skill_name: string;
  description: string;
  params: Record<string, RpaDraftParam>;
}

export interface RpaConfigParamItem {
  id: string;
  name: string;
  label: string;
  original_value: string;
  default_value: string;
  enabled: boolean;
  step_id: string;
  sensitive: boolean;
  credential_id: string;
}

export const paramItemsToDraftParams = (
  params: RpaConfigParamItem[],
  options: { includeDisabled?: boolean } = {},
): Record<string, RpaDraftParam> => {
  const paramMap: Record<string, RpaDraftParam> = {};
  params
    .filter((param) => options.includeDisabled || param.enabled)
    .forEach((param) => {
      paramMap[param.name] = {
        original_value: param.original_value,
        default_value: param.default_value || param.original_value,
        enabled: param.enabled,
        sensitive: param.sensitive || false,
        credential_id: param.credential_id || '',
        type: 'string',
        description: '',
        required: false,
      };
    });
  return paramMap;
};

export const buildRpaSkillConfigDraft = ({
  skillName,
  skillDescription,
  params,
}: {
  skillName: string;
  skillDescription: string;
  params: RpaConfigParamItem[];
}): RpaSkillConfigDraft => ({
  skill_name: skillName,
  description: skillDescription,
  params: paramItemsToDraftParams(params, { includeDisabled: true }),
});

export const draftParamsToParamItems = (
  draftParams: Record<string, RpaDraftParam>,
  generatedParams: RpaConfigParamItem[],
): RpaConfigParamItem[] => {
  const generatedByOriginalValue = new Map(
    generatedParams.map((param) => [param.original_value, param]),
  );

  return Object.entries(draftParams).map(([name, draftParam], index) => {
    const generated = generatedByOriginalValue.get(draftParam.original_value);
    return {
      id: generated?.id || `param_${index}`,
      name,
      label: generated?.label || name,
      original_value: draftParam.original_value || '',
      default_value: draftParam.default_value || draftParam.original_value || '',
      enabled: draftParam.enabled !== false,
      step_id: generated?.step_id || '',
      sensitive: !!draftParam.sensitive,
      credential_id: draftParam.credential_id || '',
    };
  });
};

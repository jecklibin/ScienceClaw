export interface RpaConfigureCopy {
  pageTitle: string;
  detailsTitle: string;
  nameLabel: string;
  purposeLabel: string;
}

export const buildRpaConfigureCopy = (isSegmentContext: boolean): RpaConfigureCopy => {
  if (isSegmentContext) {
    return {
      pageTitle: '配置片段',
      detailsTitle: '片段信息',
      nameLabel: '片段名称',
      purposeLabel: '片段用途',
    };
  }

  return {
    pageTitle: '配置技能',
    detailsTitle: '技能信息',
    nameLabel: '技能名称',
    purposeLabel: '技能描述',
  };
};

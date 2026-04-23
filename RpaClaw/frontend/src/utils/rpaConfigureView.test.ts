import { describe, expect, it } from 'vitest';

import { buildRpaConfigureCopy } from './rpaConfigureView';

describe('buildRpaConfigureCopy', () => {
  it('uses skill copy for the normal skill recording configure page', () => {
    expect(buildRpaConfigureCopy(false)).toEqual({
      pageTitle: '配置技能',
      detailsTitle: '技能信息',
      nameLabel: '技能名称',
      purposeLabel: '技能描述',
    });
  });

  it('uses segment copy for embedded conversational segment configuration', () => {
    expect(buildRpaConfigureCopy(true)).toEqual({
      pageTitle: '配置片段',
      detailsTitle: '片段信息',
      nameLabel: '片段名称',
      purposeLabel: '片段用途',
    });
  });
});

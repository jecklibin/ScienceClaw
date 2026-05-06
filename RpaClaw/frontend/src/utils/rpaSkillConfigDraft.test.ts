import { describe, expect, it } from 'vitest';
import {
  buildRpaSkillConfigDraft,
  draftParamsToParamItems,
  paramItemsToDraftParams,
  type RpaConfigParamItem,
} from './rpaSkillConfigDraft';

describe('rpaSkillConfigDraft', () => {
  it('preserves original_value and stores edited default_value separately', () => {
    const params: RpaConfigParamItem[] = [
      {
        id: 'param_0',
        name: 'query',
        label: 'Search',
        original_value: 'recorded query',
        default_value: 'configured query',
        enabled: true,
        step_id: 'step-1',
        sensitive: false,
        credential_id: '',
      },
    ];

    expect(paramItemsToDraftParams(params)).toEqual({
      query: {
        original_value: 'recorded query',
        default_value: 'configured query',
        enabled: true,
        sensitive: false,
        credential_id: '',
        type: 'string',
        description: '',
        required: false,
      },
    });
  });

  it('restores param items from draft without losing labels from generated params', () => {
    const generated: RpaConfigParamItem[] = [
      {
        id: 'param_0',
        name: 'query',
        label: 'Search',
        original_value: 'recorded query',
        default_value: 'recorded query',
        enabled: true,
        step_id: 'step-1',
        sensitive: false,
        credential_id: '',
      },
    ];

    const restored = draftParamsToParamItems(
      {
        query: {
          original_value: 'recorded query',
          default_value: 'configured query',
          enabled: true,
          sensitive: false,
          credential_id: '',
        },
      },
      generated,
    );

    expect(restored[0].label).toBe('Search');
    expect(restored[0].default_value).toBe('configured query');
  });

  it('builds a full draft payload including disabled params', () => {
    const draft = buildRpaSkillConfigDraft({
      skillName: 'Search Skill',
      skillDescription: 'Searches',
      params: [
        {
          id: 'param_0',
          name: 'query',
          label: 'Search',
          original_value: 'recorded query',
          default_value: 'configured query',
          enabled: false,
          step_id: 'step-1',
          sensitive: false,
          credential_id: '',
        },
      ],
    });

    expect(draft).toEqual({
      skill_name: 'Search Skill',
      description: 'Searches',
      params: {
        query: {
          original_value: 'recorded query',
          default_value: 'configured query',
          enabled: false,
          sensitive: false,
          credential_id: '',
          type: 'string',
          description: '',
          required: false,
        },
      },
    });
  });
});

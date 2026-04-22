import { describe, expect, it } from 'vitest'

import {
  buildMappingSourcePool,
  derivePublishSaveTarget,
  deriveSummaryInputs,
  deriveSummaryOutputs,
  mapRpaStepsToRecordingSteps,
  summarizeInputBindings,
} from './recording'
import type { RecordingSegmentSummary } from '@/types/recording'

describe('recording step mapping', () => {
  it('preserves structured RPA fields required for script generation', () => {
    const steps = mapRpaStepsToRecordingSteps([
      {
        id: 'step-1',
        action: 'navigate',
        description: '导航到 https://github.com/trending',
        target: '',
        url: 'https://github.com/trending',
        value: '',
        frame_path: ['iframe[name="preview"]'],
        result_key: 'project_name',
        validation: { status: 'ok' },
        locator_candidates: [{ kind: 'css', selected: true, locator: { method: 'css', value: 'body' } }],
        signals: { download: { filename: 'report.csv', path: '/tmp/report.csv' } },
      } as any,
      {
        id: 'step-2',
        action: 'fill',
        description: '输入 "test" 到 textbox("搜索……")',
        target: JSON.stringify({ method: 'role', role: 'textbox', name: '搜索……' }),
        value: 'test',
        url: 'https://www.runoob.com',
      },
    ])

    expect(steps[0]).toMatchObject({
      id: 'step-1',
      step_index: 0,
      action: 'navigate',
      description: '导航到 https://github.com/trending',
      target: '',
      url: 'https://github.com/trending',
      value: '',
      frame_path: ['iframe[name="preview"]'],
      result_key: 'project_name',
      signals: { download: { filename: 'report.csv', path: '/tmp/report.csv' } },
    })
    expect(steps[1]).toMatchObject({
      action: 'fill',
      value: 'test',
      url: 'https://www.runoob.com',
    })
  })

  it('derives summary inputs from params when explicit inputs are missing', () => {
    const inputs = deriveSummaryInputs({
      segment_id: 'seg-1',
      artifacts: [],
      params: {
        search: { original_value: 'test', sensitive: false },
        password: { original_value: 'secret', sensitive: true },
      },
    })

    expect(inputs).toEqual([
      expect.objectContaining({ name: 'search', type: 'string', source: 'user', default: 'test' }),
      expect.objectContaining({ name: 'password', type: 'secret', source: 'credential' }),
    ])
  })

  it('derives summary outputs from extract steps and artifacts when explicit outputs are missing', () => {
    const outputs = deriveSummaryOutputs({
      segment_id: 'seg-1',
      artifacts: [
        { name: 'downloaded_pdf', type: 'file', path: '/tmp/paper.pdf' },
      ],
      steps: [
        {
          id: 'step-1',
          action: 'extract_text',
          description: '提取项目名称',
          result_key: 'project_name',
        },
      ],
    })

    expect(outputs).toEqual([
      expect.objectContaining({ name: 'project_name', type: 'string' }),
      expect.objectContaining({ name: 'downloaded_pdf', type: 'file' }),
    ])
  })

  it('groups outputs and artifacts from any historical segment into a mapping source pool', () => {
    const summaries = [
      {
        segment_id: 'seg-1',
        title: '获取项目名称',
        outputs: [{ name: 'project_name', type: 'string', description: '项目名' }],
        artifacts: [],
      },
      {
        segment_id: 'seg-2',
        title: '下载报表',
        outputs: [],
        artifacts: [{ id: 'artifact-1', name: 'report.xlsx', type: 'file', path: '/tmp/report.xlsx' }],
      },
    ] as RecordingSegmentSummary[]

    const pool = buildMappingSourcePool({
      currentSegmentId: 'seg-3',
      summaries,
      workflowParams: [],
    })

    expect(pool.segmentOutputs).toHaveLength(1)
    expect(pool.artifacts).toHaveLength(1)
    expect(pool.segmentOutputs[0].sourceRef).toBe('seg-1.outputs.project_name')
    expect(pool.artifacts[0].sourceRef).toBe('artifact:artifact-1')
  })

  it('returns bound and unbound counts with readable summary lines', () => {
    const summary = summarizeInputBindings([
      { name: 'search', type: 'string', source: 'segment_output', source_ref: 'seg-1.outputs.project_name' },
      { name: 'report_file', type: 'file' },
    ])

    expect(summary.boundCount).toBe(1)
    expect(summary.unboundCount).toBe(1)
    expect(summary.lines[0]).toContain('search')
    expect(summary.lines[0]).toContain('seg-1.outputs.project_name')
  })

  it('derives save prompt target from publish result after confirming a draft', () => {
    const target = derivePublishSaveTarget(
      {
        prompt_kind: 'skill',
        summary: {
          name: 'multi-segment',
          draft: {
            skill_name: 'multi-segment',
          },
        },
      } as any,
      { includeDraft: true },
    )

    expect(target).toEqual({ kind: 'skill', name: 'multi-segment' })
  })

  it('falls back to draft skill name when publish result omits summary name', () => {
    const target = derivePublishSaveTarget(
      {
        prompt_kind: 'skill',
        summary: {
          draft: {
            skill_name: 'multi-segment',
          },
        },
      } as any,
      { includeDraft: true },
    )

    expect(target).toEqual({ kind: 'skill', name: 'multi-segment' })
  })
})

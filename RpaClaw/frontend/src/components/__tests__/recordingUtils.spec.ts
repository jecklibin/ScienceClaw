import { describe, expect, it } from 'vitest'

import { deriveArtifactsFromRpaSteps, mapRpaStepsToRecordingSteps } from '@/utils/recording'

describe('recording utils', () => {
  it('maps RPA steps with stable step indexes and locator metadata', () => {
    const steps = mapRpaStepsToRecordingSteps([
      {
        id: 'step-1',
        action: 'click',
        description: '点击下载按钮',
        target: '{"method":"role"}',
        validation: { status: 'ok', selected_candidate_index: 0 },
        locator_candidates: [{ kind: 'role', selected: true }],
      },
    ])

    expect(steps[0].step_index).toBe(0)
    expect(steps[0].validation?.status).toBe('ok')
    expect(steps[0].locator_candidates?.[0]?.selected).toBe(true)
  })

  it('derives text artifacts from extracted step outputs', () => {
    const artifacts = deriveArtifactsFromRpaSteps([
      {
        id: 'step-1',
        action: 'extract',
        result_key: 'downloaded_title',
        value: 'A paper',
      },
    ])

    expect(artifacts).toEqual([
      {
        name: 'downloaded_title',
        type: 'text',
        value: 'A paper',
        labels: ['recording', 'extracted'],
      },
    ])
  })

  it('derives file artifacts from download signals', () => {
    const artifacts = deriveArtifactsFromRpaSteps([
      {
        id: 'step-2',
        action: 'click',
        value: 'paper.pdf',
        signals: {
          download: {
            filename: 'paper.pdf',
            path: '/tmp/paper.pdf',
          },
        },
      },
    ])

    expect(artifacts).toEqual([
      {
        name: 'paper.pdf',
        type: 'file',
        path: '/tmp/paper.pdf',
        labels: ['recording', 'download'],
      },
    ])
  })
})

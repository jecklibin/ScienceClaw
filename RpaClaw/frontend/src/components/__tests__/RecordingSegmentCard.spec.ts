// @vitest-environment jsdom

import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/recording', () => ({
  promoteRecordingStepLocator: vi.fn(),
}))

describe('RecordingSegmentCard', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('renders a collapsed recording summary by default and expands steps on demand', async () => {
    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      summary: {
        segment_id: 'seg-1',
        intent: 'Download GitHub trend',
        kind: 'rpa',
        status: 'completed',
        params: {
          keyword: { original_value: 'paper', sensitive: false },
          password: { original_value: 'secret', sensitive: true, credential_id: 'cred-1' },
        },
        auth_config: { credential_ids: ['cred-1'] },
        testing_status: 'passed',
        artifacts: [],
        steps: [
          {
            id: 'step-1',
            step_index: 0,
            action: 'navigate',
            description: 'Open GitHub Trending',
            target: 'https://github.com/trending',
          },
        ],
      },
    })
    app.mount(root)
    await nextTick()

    expect(root.textContent).toContain('Download GitHub trend')
    expect(root.textContent).toContain('1 steps')
    expect(root.textContent).toContain('2 params')
    expect(root.textContent).toContain('auth ready')
    expect(root.textContent).toContain('tested')
    expect(root.textContent).not.toContain('Open GitHub Trending')

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()

    expect(root.textContent).toContain('Open GitHub Trending')
    app.unmount()
  })

  it('renders a script segment as a workflow segment card', async () => {
    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      summary: {
        segment_id: 'segment_2',
        kind: 'script',
        title: '转换报表',
        description: '将下载文件转换为 CSV',
        artifacts: [],
        steps: [],
        params: {},
        testing_status: 'passed',
        inputs: [{ name: 'source_file', type: 'file' }],
        outputs: [{ name: 'converted_csv', type: 'file' }],
      },
    })
    app.mount(root)
    await nextTick()

    expect(root.textContent).toContain('Script segment')
    expect(root.textContent).toContain('转换报表')
    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()
    expect(root.textContent).toContain('source_file')
    expect(root.textContent).toContain('converted_csv')

    app.unmount()
  })
})

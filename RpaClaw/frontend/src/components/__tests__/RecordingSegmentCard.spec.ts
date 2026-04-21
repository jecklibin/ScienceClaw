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
    expect(root.textContent).not.toContain('Open GitHub Trending')

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()

    expect(root.textContent).toContain('Open GitHub Trending')
    app.unmount()
  })
})

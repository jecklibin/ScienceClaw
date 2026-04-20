import { describe, expect, it } from 'vitest'

import { createRecordingRunStore } from '@/composables/useRecordingRun'

describe('createRecordingRunStore', () => {
  it('creates a full-screen recorder route on recording_run_started and clears it on segment completion', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: '下载 PDF' },
      open_workbench: true,
    })

    expect(store.workbenchOpen.value).toBe(false)
    expect(store.fullPageRecorderRoute.value?.path).toBe('/rpa/recorder')
    expect(store.fullPageRecorderRoute.value?.query.chatSessionId).toBe('chat-1')
    expect(store.activeSegment.value?.id).toBe('seg-1')

    store.onSegmentCompleted({
      segment: { id: 'seg-1', status: 'completed' },
      summary: {
        segment_id: 'seg-1',
        intent: '下载 PDF',
        artifacts: [{ name: 'downloaded_pdf', type: 'file', path: '/tmp/paper.pdf' }],
      },
    })

    expect(store.workbenchOpen.value).toBe(false)
    expect(store.fullPageRecorderRoute.value).toBeNull()
    expect(store.activeSegment.value).toBeNull()
    expect(store.artifacts.value[0].name).toBe('downloaded_pdf')
    expect(store.summaries.value[0].segment_id).toBe('seg-1')
  })
})

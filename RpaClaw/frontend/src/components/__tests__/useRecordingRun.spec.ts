import { describe, expect, it } from 'vitest'

import { createRecordingRunStore } from '@/composables/useRecordingRun'

describe('createRecordingRunStore', () => {
  it('creates a full-screen recorder route on recording_run_started and clears it on segment completion', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: 'download PDF' },
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
        intent: 'download PDF',
        session_id: 'rpa-1',
        artifacts: [{ name: 'downloaded_pdf', type: 'file', path: '/tmp/paper.pdf' }],
      },
    })

    expect(store.workbenchOpen.value).toBe(false)
    expect(store.fullPageRecorderRoute.value).toBeNull()
    expect(store.activeSegment.value).toBeNull()
    expect(store.artifacts.value[0].name).toBe('downloaded_pdf')
    expect(store.summaries.value[0].segment_id).toBe('seg-1')
    expect(store.actionPrompt.value).toMatchObject({
      runId: 'run-1',
      segmentId: 'seg-1',
      rpaSessionId: 'rpa-1',
    })
  })

  it('appends completed segments in chronological order for bottom-of-chat rendering', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: 'first' },
      open_workbench: false,
    })
    store.onSegmentCompleted({
      segment: { id: 'seg-1', status: 'completed' },
      summary: { segment_id: 'seg-1', intent: 'first', artifacts: [] },
    })
    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-2', status: 'recording', kind: 'rpa', intent: 'second' },
      open_workbench: false,
    })
    store.onSegmentCompleted({
      segment: { id: 'seg-2', status: 'completed' },
      summary: { segment_id: 'seg-2', intent: 'second', artifacts: [] },
    })

    expect(store.summaries.value.map((summary) => summary.segment_id)).toEqual(['seg-1', 'seg-2'])
  })
})

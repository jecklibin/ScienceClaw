import { describe, expect, it } from 'vitest'

import { createRecordingRunStore } from '@/composables/useRecordingRun'

describe('createRecordingRunStore', () => {
  it('opens workbench on recording_run_started and collapses on segment completion', () => {
    const store = createRecordingRunStore()

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: '下载 PDF' },
      open_workbench: true,
    })

    expect(store.workbenchOpen.value).toBe(true)
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
    expect(store.activeSegment.value).toBeNull()
    expect(store.artifacts.value[0].name).toBe('downloaded_pdf')
    expect(store.summaries.value[0].segment_id).toBe('seg-1')
  })
})

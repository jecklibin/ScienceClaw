import { describe, expect, it } from 'vitest'

import { createRecordingRunStore } from '@/composables/useRecordingRun'

describe('recording lifecycle store', () => {
  it('routes interactive segments to the full-screen recorder instead of opening an embedded workbench', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: '下载 PDF' },
      open_workbench: true,
    })
    expect(store.workbenchOpen.value).toBe(false)
    expect(store.fullPageRecorderRoute.value).toMatchObject({
      path: '/rpa/recorder',
      query: {
        chatSessionId: 'chat-1',
        runId: 'run-1',
        segmentId: 'seg-1',
      },
    })

    store.onSegmentCompleted({
      segment: { id: 'seg-1', status: 'completed' },
      summary: { segment_id: 'seg-1', artifacts: [], steps: [] },
    })
    expect(store.workbenchOpen.value).toBe(false)
  })

  it('tracks testing and publish prompt state', () => {
    const store = createRecordingRunStore()

    store.onTestStarted({
      run: { id: 'run-1', status: 'testing', type: 'rpa', testing: { status: 'running' } },
      test_payload: { steps: [] },
    })
    expect(store.testingState.value.status).toBe('running')
    expect(store.workbenchOpen.value).toBe(true)

    store.onPublishPrepared({
      run: { id: 'run-1', status: 'ready_to_publish', type: 'rpa', publish_target: 'skill' },
      prompt_kind: 'skill',
      staging_paths: ['/workspace/session-1/skills_staging/run-1'],
      summary: { name: 'recorded_workflow' },
    })
    expect(store.run.value?.publish_target).toBe('skill')
    expect(store.publishPrompt.value?.kind).toBe('skill')
    expect(store.publishPrompt.value?.name).toBe('recorded_workflow')
  })
})

import { describe, expect, it } from 'vitest'

import { createRecordingRunStore } from '@/composables/useRecordingRun'

describe('createRecordingRunStore', () => {
  it('creates an overlay recorder route on recording_run_started and clears it on segment completion', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: 'download PDF' },
      open_workbench: true,
    })

    expect(store.workbenchOpen.value).toBe(false)
    expect(store.recorderModalOpen.value).toBe(true)
    expect(store.recorderModalRoute.value?.path).toBe('/rpa/recorder')
    expect(store.recorderModalRoute.value?.query.chatSessionId).toBe('chat-1')
    expect(store.recorderModalRoute.value?.query.embedded).toBe('1')
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
    expect(store.recorderModalOpen.value).toBe(false)
    expect(store.recorderModalRoute.value).toBeNull()
    expect(store.activeSegment.value).toBeNull()
    expect(store.artifacts.value[0].name).toBe('downloaded_pdf')
    expect(store.summaries.value[0].segment_id).toBe('seg-1')
    expect(store.actionPrompt.value).toMatchObject({
      runId: 'run-1',
      segmentId: 'seg-1',
      rpaSessionId: 'rpa-1',
    })
  })

  it('resolves the current chat session lazily when opening the recorder overlay', () => {
    let currentSessionId = 'chat-1'
    const store = createRecordingRunStore(() => currentSessionId)

    currentSessionId = 'chat-2'
    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: 'download PDF' },
      open_workbench: true,
    })

    expect(store.recorderModalRoute.value?.query.chatSessionId).toBe('chat-2')
    expect(store.recorderModalRoute.value?.query.returnTo).toBe('/chat/chat-2')
  })

  it('keeps the modal open and moves to configuration after embedded recording capture', () => {
    const store = createRecordingRunStore('chat-1')

    store.onRunStarted({
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: 'download PDF' },
      open_workbench: true,
    })

    store.onRecordingCaptured({
      rpaSessionId: 'rpa-1',
      steps: [{ id: 'step-1', step_index: 0, action: 'navigate' }],
      artifacts: [],
    })

    expect(store.recorderModalOpen.value).toBe(true)
    expect(store.recorderModalRoute.value).toMatchObject({
      path: '/rpa/configure',
      query: {
        sessionId: 'rpa-1',
        chatSessionId: 'chat-1',
        runId: 'run-1',
        segmentId: 'seg-1',
        embedded: '1',
      },
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

  it('stores publish draft from recording publish prepared event', () => {
    const store = createRecordingRunStore('chat-1')

    store.onPublishPrepared({
      run: { id: 'run-1', status: 'ready_to_publish', type: 'rpa' },
      prompt_kind: 'skill',
      staging_paths: [],
      summary: {
        name: 'download_and_convert_report',
        draft: {
          id: 'draft_run-1',
          run_id: 'run-1',
          publish_target: 'skill',
          skill_name: 'download_and_convert_report',
          display_title: '下载并转换业务报表',
          description: '自动下载业务报表并转换为 CSV。',
          trigger_examples: [],
          inputs: [],
          outputs: [],
          credentials: [],
          segments: [],
          warnings: [],
        },
      },
    })

    expect(store.publishDraft.value?.skill_name).toBe('download_and_convert_report')
  })

  it('opens the embedded test route when recording testing starts from chat', () => {
    const store = createRecordingRunStore('chat-1')

    store.onSegmentCompleted({
      segment: { id: 'seg-1', status: 'completed' },
      summary: { segment_id: 'seg-1', session_id: 'rpa-1', artifacts: [] },
    })

    store.onTestStarted({
      run: { id: 'run-1', status: 'testing', type: 'rpa', testing: { status: 'running' } },
      test_payload: {
        rpa_session_id: 'rpa-1',
        segment_id: 'seg-1',
        title: '下载 PDF',
        description: '下载并检查 PDF 文件',
        params: { file_name: { original_value: 'paper.pdf' } },
      },
    })

    expect(store.recorderModalOpen.value).toBe(true)
    expect(store.recorderModalRoute.value).toMatchObject({
      path: '/rpa/test',
      query: {
        sessionId: 'rpa-1',
        chatSessionId: 'chat-1',
        runId: 'run-1',
        segmentId: 'seg-1',
        embedded: '1',
      },
    })
  })

  it('updates existing segment summary when bindings are changed from chat', () => {
    const store = createRecordingRunStore('chat-1')

    store.onSegmentCompleted({
      segment: { id: 'seg-1', status: 'completed' },
      summary: { segment_id: 'seg-1', intent: 'first', artifacts: [] },
    })
    store.onSegmentCompleted({
      segment: { id: 'seg-2', status: 'completed' },
      summary: { segment_id: 'seg-2', intent: 'second', artifacts: [] },
    })

    store.onSegmentUpdated({
      run: { id: 'run-1', status: 'ready_for_next_segment', type: 'rpa' },
      summaries: [
        {
          segment_id: 'seg-1',
          outputs: [{ name: 'issue_title', type: 'string' }],
          artifacts: [],
        },
        {
          segment_id: 'seg-2',
          inputs: [{ name: 'query', type: 'string', source_ref: 'seg-1.outputs.issue_title' }],
          artifacts: [],
        },
      ],
    })

    expect(store.summaries.value[0].outputs?.[0].name).toBe('issue_title')
    expect(store.summaries.value[1].inputs?.[0].source_ref).toBe('seg-1.outputs.issue_title')
  })
})

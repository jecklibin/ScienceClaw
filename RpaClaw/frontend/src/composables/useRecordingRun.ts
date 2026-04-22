import { computed, ref } from 'vue'

import type {
  RecordingArtifact,
  RecordingRun,
  RecordingRunStartedPayload,
  RecordingSegment,
  RecordingSegmentCapturedPayload,
  RecordingSegmentCompletedPayload,
  RecordingSegmentUpdatedPayload,
  RecordingSegmentSummary,
  RecordingTestStartedPayload,
  RecordingPublishPreparedPayload,
  SkillPublishDraft,
} from '@/types/recording'

export interface RecordingActionPrompt {
  runId: string
  segmentId: string
  rpaSessionId?: string
  intent?: string
  publishTarget: 'skill' | 'tool'
  testingStatus?: string
}

type ChatSessionIdSource = string | null | undefined | (() => string | null | undefined)
type RecordingEventOptions = {
  interactive?: boolean
  openModal?: boolean
}

export function createRecordingRunStore(chatSessionIdSource?: ChatSessionIdSource) {
  const run = ref<RecordingRun | null>(null)
  const activeSegment = ref<RecordingSegment | null>(null)
  const artifacts = ref<RecordingArtifact[]>([])
  const summaries = ref<RecordingSegmentSummary[]>([])
  const workbenchOpen = ref(false)
  const recorderModalOpen = ref(false)
  const recorderModalRoute = ref<{
    path: string
    query: Record<string, string>
  } | null>(null)
  const publishDraft = ref<SkillPublishDraft | null>(null)
  const actionPrompt = ref<RecordingActionPrompt | null>(null)

  const canContinue = computed(() => !!run.value && !activeSegment.value)
  const testingState = computed(() => run.value?.testing || { status: run.value?.status === 'testing' ? 'running' : 'idle' })
  const getChatSessionId = () => (
    typeof chatSessionIdSource === 'function' ? chatSessionIdSource() : chatSessionIdSource
  )
  const buildRouteContext = () => {
    const currentChatSessionId = getChatSessionId() || ''
    return {
      chatSessionId: currentChatSessionId,
      returnTo: currentChatSessionId ? `/chat/${currentChatSessionId}` : '/chat',
    }
  }

  const onRunStarted = (payload: RecordingRunStartedPayload, options: RecordingEventOptions = {}) => {
    const interactive = options.interactive ?? true
    run.value = payload.run
    activeSegment.value = payload.segment
    actionPrompt.value = null
    publishDraft.value = null
    workbenchOpen.value = false
    const routeContext = buildRouteContext()
    const route = interactive && payload.open_workbench
      ? {
          path: '/rpa/recorder',
          query: {
            sandboxId: `recording-${payload.run.id}`,
            chatSessionId: routeContext.chatSessionId,
            runId: payload.run.id,
            segmentId: payload.segment.id,
            returnTo: routeContext.returnTo,
          embedded: '1',
        },
      }
      : null
    recorderModalRoute.value = route
    recorderModalOpen.value = !!route
  }

  const onRecordingCaptured = (payload: RecordingSegmentCapturedPayload, options: RecordingEventOptions = {}) => {
    const interactive = options.interactive ?? true
    if (!interactive || !run.value || !activeSegment.value) return
    const routeContext = buildRouteContext()
    const sharedQuery = {
      sessionId: payload.rpaSessionId,
      chatSessionId: routeContext.chatSessionId,
      runId: run.value.id,
      segmentId: activeSegment.value.id,
      returnTo: routeContext.returnTo,
      embedded: '1',
    }
    if (run.value.publish_target === 'tool') {
      recorderModalRoute.value = {
        path: '/rpa/segment-configure',
        query: {
          ...sharedQuery,
        },
      }
      recorderModalOpen.value = true
      return
    }
    recorderModalRoute.value = {
      path: '/rpa/configure',
      query: sharedQuery,
    }
    recorderModalOpen.value = true
  }

  const onSegmentCompleted = (payload: RecordingSegmentCompletedPayload, options: RecordingEventOptions = {}) => {
    const interactive = options.interactive ?? true
    activeSegment.value = null
    workbenchOpen.value = false
    recorderModalOpen.value = false
    recorderModalRoute.value = null
    const summaryIndex = summaries.value.findIndex((item) => item.segment_id === payload.summary.segment_id)
    summaries.value = summaryIndex >= 0
      ? summaries.value.map((item, index) => index === summaryIndex ? { ...item, ...payload.summary } : item)
      : [...summaries.value, payload.summary]
    const existingArtifactKeys = new Set(
      artifacts.value.map((artifact) => artifact.id || `${artifact.type}:${artifact.path || artifact.name}`),
    )
    const nextArtifacts = [...artifacts.value]
    for (const artifact of payload.summary.artifacts) {
      const key = artifact.id || `${artifact.type}:${artifact.path || artifact.name}`
      if (!existingArtifactKeys.has(key)) {
        existingArtifactKeys.add(key)
        nextArtifacts.push(artifact)
      }
    }
    artifacts.value = nextArtifacts
    if (run.value) {
      run.value = { ...run.value, status: 'ready_for_next_segment' }
      actionPrompt.value = interactive ? {
        runId: run.value.id,
        segmentId: payload.segment.id || payload.summary.segment_id,
        rpaSessionId: payload.summary.session_id,
        intent: payload.summary.intent,
        publishTarget: run.value.publish_target || 'skill',
        testingStatus: run.value.testing?.status || 'idle',
      } : null
    }
  }

  const onSegmentUpdated = (payload: RecordingSegmentUpdatedPayload) => {
    run.value = payload.run
    const next = [...summaries.value]
    for (const summary of payload.summaries) {
      const index = next.findIndex((item) => item.segment_id === summary.segment_id)
      if (index >= 0) {
        next[index] = { ...next[index], ...summary }
      } else {
        next.push(summary)
      }
    }
    summaries.value = next
  }

  const onTestStarted = (payload: RecordingTestStartedPayload, options: RecordingEventOptions = {}) => {
    const interactive = options.interactive ?? true
    const openModal = options.openModal ?? interactive
    run.value = payload.run
    workbenchOpen.value = false
    recorderModalOpen.value = false
    recorderModalRoute.value = null
    if (!interactive) {
      return
    }
    const routeContext = buildRouteContext()
    const testMode = typeof payload.test_payload?.mode === 'string'
      ? payload.test_payload.mode
      : 'segment'
    if (testMode === 'workflow') {
      if (actionPrompt.value) {
        actionPrompt.value = {
          ...actionPrompt.value,
          testingStatus: payload.run.testing?.status || 'running',
        }
      }
      return
    }
    if (!openModal) {
      if (actionPrompt.value) {
        actionPrompt.value = {
          ...actionPrompt.value,
          testingStatus: payload.run.testing?.status || 'running',
        }
      }
      return
    }

    const rpaSessionId = typeof payload.test_payload?.rpa_session_id === 'string'
      ? payload.test_payload.rpa_session_id
      : ''
    const segmentId = typeof payload.test_payload?.segment_id === 'string'
      ? payload.test_payload.segment_id
      : actionPrompt.value?.segmentId || ''
    const query: Record<string, string> | null = rpaSessionId
      ? {
          sessionId: rpaSessionId,
          chatSessionId: routeContext.chatSessionId,
          runId: payload.run.id,
          segmentId,
          returnTo: routeContext.returnTo,
          embedded: '1',
          segmentTitle: typeof payload.test_payload?.title === 'string' ? payload.test_payload.title : '',
          segmentPurpose: typeof payload.test_payload?.description === 'string' ? payload.test_payload.description : '',
          params: JSON.stringify(payload.test_payload?.params || {}),
        }
      : null
    recorderModalRoute.value = query
      ? {
          path: '/rpa/test',
          query,
        }
      : null
    recorderModalOpen.value = !!query
    if (actionPrompt.value) {
      actionPrompt.value = {
        ...actionPrompt.value,
        testingStatus: payload.run.testing?.status || 'running',
      }
    }
  }

  const onPublishPrepared = (payload: RecordingPublishPreparedPayload, options: RecordingEventOptions = {}) => {
    const interactive = options.interactive ?? true
    run.value = payload.run
    actionPrompt.value = null
    publishDraft.value = interactive ? payload.summary.draft || null : null
  }

  const setPublishDraft = (draft: SkillPublishDraft | null) => {
    publishDraft.value = draft
  }

  const closeWorkbench = () => {
    workbenchOpen.value = false
  }

  const openWorkbench = () => {
    workbenchOpen.value = true
  }

  const closeRecorderModal = () => {
    recorderModalOpen.value = false
    recorderModalRoute.value = null
  }

  const dismissActionPrompt = () => {
    actionPrompt.value = null
  }

  const reset = () => {
    run.value = null
    activeSegment.value = null
    artifacts.value = []
    summaries.value = []
    workbenchOpen.value = false
    recorderModalOpen.value = false
    recorderModalRoute.value = null
    publishDraft.value = null
    actionPrompt.value = null
  }

  return {
    run,
    activeSegment,
    artifacts,
    summaries,
    workbenchOpen,
    recorderModalOpen,
    recorderModalRoute,
    actionPrompt,
    publishDraft,
    testingState,
    canContinue,
    onRunStarted,
    onRecordingCaptured,
    onSegmentCompleted,
    onSegmentUpdated,
    onTestStarted,
    onPublishPrepared,
    setPublishDraft,
    closeWorkbench,
    openWorkbench,
    closeRecorderModal,
    dismissActionPrompt,
    reset,
  }
}

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

export function createRecordingRunStore(chatSessionIdSource?: ChatSessionIdSource) {
  const run = ref<RecordingRun | null>(null)
  const activeSegment = ref<RecordingSegment | null>(null)
  const artifacts = ref<RecordingArtifact[]>([])
  const summaries = ref<RecordingSegmentSummary[]>([])
  const workbenchOpen = ref(false)
  const fullPageRecorderRoute = ref<{
    path: string
    query: Record<string, string>
  } | null>(null)
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

  const onRunStarted = (payload: RecordingRunStartedPayload) => {
    run.value = payload.run
    activeSegment.value = payload.segment
    actionPrompt.value = null
    workbenchOpen.value = false
    const routeContext = buildRouteContext()
    const route = payload.open_workbench
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
    fullPageRecorderRoute.value = route
    recorderModalRoute.value = route
    recorderModalOpen.value = !!route
  }

  const onRecordingCaptured = (payload: RecordingSegmentCapturedPayload) => {
    if (!run.value || !activeSegment.value) return
    const routeContext = buildRouteContext()
    recorderModalRoute.value = {
      path: '/rpa/configure',
      query: {
        sessionId: payload.rpaSessionId,
        chatSessionId: routeContext.chatSessionId,
        runId: run.value.id,
        segmentId: activeSegment.value.id,
        returnTo: routeContext.returnTo,
        embedded: '1',
      },
    }
    recorderModalOpen.value = true
  }

  const onSegmentCompleted = (payload: RecordingSegmentCompletedPayload) => {
    activeSegment.value = null
    workbenchOpen.value = false
    fullPageRecorderRoute.value = null
    recorderModalOpen.value = false
    recorderModalRoute.value = null
    summaries.value = [...summaries.value, payload.summary]
    artifacts.value = [...artifacts.value, ...payload.summary.artifacts]
    if (run.value) {
      run.value = { ...run.value, status: 'ready_for_next_segment' }
      actionPrompt.value = {
        runId: run.value.id,
        segmentId: payload.segment.id || payload.summary.segment_id,
        rpaSessionId: payload.summary.session_id,
        intent: payload.summary.intent,
        publishTarget: run.value.publish_target || 'skill',
        testingStatus: run.value.testing?.status || 'idle',
      }
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

  const onTestStarted = (payload: RecordingTestStartedPayload) => {
    run.value = payload.run
    workbenchOpen.value = false
    if (actionPrompt.value) {
      actionPrompt.value = {
        ...actionPrompt.value,
        testingStatus: payload.run.testing?.status || 'running',
      }
    }
  }

  const onPublishPrepared = (payload: RecordingPublishPreparedPayload) => {
    run.value = payload.run
    actionPrompt.value = null
    publishDraft.value = payload.summary.draft || null
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

  const consumeFullPageRecorderRoute = () => {
    const route = fullPageRecorderRoute.value
    fullPageRecorderRoute.value = null
    return route
  }

  return {
    run,
    activeSegment,
    artifacts,
    summaries,
    workbenchOpen,
    fullPageRecorderRoute,
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
    consumeFullPageRecorderRoute,
  }
}

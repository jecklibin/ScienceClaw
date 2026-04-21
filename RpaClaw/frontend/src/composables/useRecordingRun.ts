import { computed, ref } from 'vue'

import type {
  RecordingArtifact,
  RecordingRun,
  RecordingRunStartedPayload,
  RecordingSegment,
  RecordingSegmentCompletedPayload,
  RecordingSegmentSummary,
  RecordingTestStartedPayload,
  RecordingPublishPreparedPayload,
} from '@/types/recording'

export interface RecordingActionPrompt {
  runId: string
  segmentId: string
  rpaSessionId?: string
  intent?: string
  publishTarget: 'skill' | 'tool'
  testingStatus?: string
}

export function createRecordingRunStore(chatSessionId?: string) {
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
  const publishPrompt = ref<{ kind: 'skill' | 'tool'; name: string; stagingPaths: string[] } | null>(null)
  const actionPrompt = ref<RecordingActionPrompt | null>(null)

  const canContinue = computed(() => !!run.value && !activeSegment.value)
  const testingState = computed(() => run.value?.testing || { status: run.value?.status === 'testing' ? 'running' : 'idle' })

  const onRunStarted = (payload: RecordingRunStartedPayload) => {
    run.value = payload.run
    activeSegment.value = payload.segment
    actionPrompt.value = null
    workbenchOpen.value = false
    const route = payload.open_workbench
      ? {
          path: '/rpa/recorder',
          query: {
            sandboxId: `recording-${payload.run.id}`,
            chatSessionId: chatSessionId || '',
            runId: payload.run.id,
            segmentId: payload.segment.id,
            returnTo: chatSessionId ? `/chat/${chatSessionId}` : '/chat',
            embedded: '1',
          },
        }
      : null
    fullPageRecorderRoute.value = route
    recorderModalRoute.value = route
    recorderModalOpen.value = !!route
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
    publishPrompt.value = {
      kind: payload.prompt_kind,
      name: payload.summary.name || payload.summary.title || 'recorded_workflow',
      stagingPaths: payload.staging_paths,
    }
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
    testingState,
    publishPrompt,
    canContinue,
    onRunStarted,
    onSegmentCompleted,
    onTestStarted,
    onPublishPrepared,
    closeWorkbench,
    openWorkbench,
    closeRecorderModal,
    dismissActionPrompt,
    consumeFullPageRecorderRoute,
  }
}

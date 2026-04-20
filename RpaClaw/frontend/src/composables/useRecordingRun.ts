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

export function createRecordingRunStore() {
  const run = ref<RecordingRun | null>(null)
  const activeSegment = ref<RecordingSegment | null>(null)
  const artifacts = ref<RecordingArtifact[]>([])
  const summaries = ref<RecordingSegmentSummary[]>([])
  const workbenchOpen = ref(false)
  const publishPrompt = ref<{ kind: 'skill' | 'tool'; name: string; stagingPaths: string[] } | null>(null)

  const canContinue = computed(() => !!run.value && !activeSegment.value)
  const testingState = computed(() => run.value?.testing || { status: run.value?.status === 'testing' ? 'running' : 'idle' })

  const onRunStarted = (payload: RecordingRunStartedPayload) => {
    run.value = payload.run
    activeSegment.value = payload.segment
    workbenchOpen.value = payload.open_workbench
  }

  const onSegmentCompleted = (payload: RecordingSegmentCompletedPayload) => {
    activeSegment.value = null
    workbenchOpen.value = false
    summaries.value = [payload.summary, ...summaries.value]
    artifacts.value = [...payload.summary.artifacts, ...artifacts.value]
    if (run.value) {
      run.value = { ...run.value, status: 'ready_for_next_segment' }
    }
  }

  const onTestStarted = (payload: RecordingTestStartedPayload) => {
    run.value = payload.run
    workbenchOpen.value = true
  }

  const onPublishPrepared = (payload: RecordingPublishPreparedPayload) => {
    run.value = payload.run
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

  return {
    run,
    activeSegment,
    artifacts,
    summaries,
    workbenchOpen,
    testingState,
    publishPrompt,
    canContinue,
    onRunStarted,
    onSegmentCompleted,
    onTestStarted,
    onPublishPrepared,
    closeWorkbench,
    openWorkbench,
  }
}

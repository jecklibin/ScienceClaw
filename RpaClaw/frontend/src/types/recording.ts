export type RecordingRunStatus =
  | 'draft'
  | 'recording'
  | 'waiting_user'
  | 'processing_artifacts'
  | 'ready_for_next_segment'
  | 'testing'
  | 'needs_repair'
  | 'ready_to_publish'
  | 'blocked'
  | 'failed'
  | 'completed'
  | 'saved'

export interface RecordingRun {
  id: string
  status: RecordingRunStatus
  type?: 'rpa' | 'mcp' | 'mixed'
  publish_target?: 'skill' | 'tool' | null
  testing?: {
    status: 'idle' | 'running' | 'failed' | 'passed' | string
    failed_step_index?: number | null
    error?: string
  }
}

export interface RecordingSegment {
  id: string
  status: string
  kind?: 'rpa' | 'mcp' | 'chat_process' | 'mixed'
  intent?: string
}

export interface RecordingArtifact {
  id?: string
  name: string
  type: 'file' | 'text' | 'json' | 'table'
  path?: string
  value?: unknown
  labels?: string[]
}

export interface RecordingStepCandidate {
  kind?: string
  status?: string
  selected?: boolean
  locator?: Record<string, unknown>
  reason?: string
}

export interface RecordingStep {
  id: string
  step_index?: number
  action: string
  description?: string
  target?: string
  validation?: {
    status?: string
    details?: string
    selected_candidate_index?: number
    selected_candidate_kind?: string
  }
  locator_candidates?: RecordingStepCandidate[]
}

export interface RecordingSegmentSummary {
  segment_id: string
  session_id?: string
  intent?: string
  kind?: string
  status?: string
  artifacts: RecordingArtifact[]
  steps?: RecordingStep[]
}

export interface RecordingRunStartedPayload {
  run: RecordingRun
  segment: RecordingSegment
  open_workbench: boolean
}

export interface RecordingSegmentCompletedPayload {
  segment: RecordingSegment
  summary: RecordingSegmentSummary
}

export interface RecordingTestStartedPayload {
  run: RecordingRun
  test_payload: Record<string, unknown>
}

export interface RecordingPublishPreparedPayload {
  run: RecordingRun
  prompt_kind: 'skill' | 'tool'
  staging_paths: string[]
  summary: {
    name?: string
    title?: string
    [key: string]: unknown
  }
}

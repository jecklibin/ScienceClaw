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

export type RecordingParamConfig = Record<string, {
  original_value?: unknown
  sensitive?: boolean
  credential_id?: string
}>

export interface RecordingSegmentSummary {
  segment_id: string
  session_id?: string
  intent?: string
  title?: string
  description?: string
  kind?: string
  status?: string
  params?: RecordingParamConfig
  auth_config?: Record<string, unknown>
  testing_status?: string
  artifacts: RecordingArtifact[]
  steps?: RecordingStep[]
  inputs?: WorkflowIO[]
  outputs?: WorkflowIO[]
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

export interface RecordingSegmentCapturedPayload {
  rpaSessionId: string
  steps: RecordingStep[]
  artifacts: RecordingArtifact[]
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
    draft?: SkillPublishDraft
    [key: string]: unknown
  }
}

export interface RecordingSegmentUpdatedPayload {
  run: RecordingRun
  summaries: RecordingSegmentSummary[]
}

export type WorkflowSegmentKind = 'rpa' | 'script' | 'mcp' | 'llm' | 'mixed'
export type WorkflowValueType = 'string' | 'number' | 'boolean' | 'file' | 'json' | 'secret'

export interface WorkflowIO {
  name: string
  type: WorkflowValueType
  required?: boolean
  source?: 'user' | 'workflow_param' | 'segment_output' | 'artifact' | 'credential'
  source_ref?: string | null
  description?: string
  default?: unknown
}

export interface WorkflowPublishSegmentSummary {
  id: string
  kind: WorkflowSegmentKind
  title: string
  purpose: string
  status: string
  input_count: number
  output_count: number
}

export interface WorkflowCredentialRequirement {
  name: string
  type: 'browser_session' | 'api_key' | 'username_password' | 'oauth' | 'secret'
  description: string
}

export interface WorkflowPublishWarning {
  code: string
  message: string
  segment_id?: string | null
}

export interface SkillPublishDraft {
  id: string
  run_id: string
  publish_target: 'skill' | 'tool' | 'mcp'
  skill_name: string
  display_title: string
  description: string
  trigger_examples: string[]
  inputs: WorkflowIO[]
  outputs: WorkflowIO[]
  credentials: WorkflowCredentialRequirement[]
  segments: WorkflowPublishSegmentSummary[]
  warnings: WorkflowPublishWarning[]
}

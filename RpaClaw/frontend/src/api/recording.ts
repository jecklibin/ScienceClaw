import { apiClient } from '@/api/client'
import type {
  MappingSourcePool,
  RecordingArtifact,
  RecordingParamConfig,
  RecordingRun,
  RecordingStep,
  RecordingSegmentSummary,
  SkillPublishDraft,
  WorkflowIO,
} from '@/types/recording'

export async function createRecordingRun(
  sessionId: string,
  message: string,
  options: {
    kind?: 'rpa' | 'mcp' | 'mixed'
    publish_target?: 'skill' | 'tool'
    requires_workbench?: boolean
  } = {},
) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings`, {
    message,
    ...options,
  })
  return response.data.data
}

export async function completeRecordingSegment(
  sessionId: string,
  runId: string,
  segmentId: string,
  payload: {
    rpa_session_id?: string
    steps: RecordingStep[]
    artifacts: RecordingArtifact[]
    params?: RecordingParamConfig
    auth_config?: Record<string, unknown>
    inputs?: WorkflowIO[]
    outputs?: WorkflowIO[]
    title?: string
    description?: string
    testing_status?: string
  },
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/segments/${segmentId}/complete`,
    payload,
  )
  return response.data.data
}

export async function promoteRecordingStepLocator(
  rpaSessionId: string,
  stepIndex: number,
  candidateIndex: number,
) {
  const response = await apiClient.post(
    `/rpa/session/${rpaSessionId}/step/${stepIndex}/locator`,
    { candidate_index: candidateIndex },
  )
  return response.data.step
}

export async function promoteRecordingSegmentStepLocator(
  sessionId: string,
  runId: string,
  segmentId: string,
  stepIndex: number,
  candidateIndex: number,
  rpaSessionId?: string,
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/segments/${segmentId}/steps/${stepIndex}/locator`,
    { candidate_index: candidateIndex, rpa_session_id: rpaSessionId },
  )
  return response.data.data as {
    run: RecordingRun
    segment: Record<string, unknown>
    summary: RecordingSegmentSummary
    step: RecordingStep
  }
}

export async function testRecordingRun(sessionId: string, runId: string) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings/${runId}/test`)
  return response.data.data
}

export async function executeWorkflowRecordingTest(sessionId: string, runId: string) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings/${runId}/workflow-test`)
  return response.data.data
}

export async function publishRecordingRun(
  sessionId: string,
  runId: string,
  publishTarget: 'skill' | 'tool' | 'mcp',
  draft?: SkillPublishDraft,
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/publish`,
    { publish_target: publishTarget, draft },
  )
  return response.data.data
}

export async function prepareRecordingPublishDraft(
  sessionId: string,
  runId: string,
  publishTarget: 'skill' | 'tool' | 'mcp',
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/publish-draft`,
    { publish_target: publishTarget },
  )
  return response.data.data as { draft: SkillPublishDraft }
}

export async function createScriptRecordingSegment(
  sessionId: string,
  runId: string,
  payload: {
    title: string
    purpose: string
    script: string
    entry?: string
    params?: Record<string, unknown>
    inputs?: Array<Record<string, unknown>>
    outputs?: Array<Record<string, unknown>>
  },
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/script-segments`,
    payload,
  )
  return response.data.data
}

const normalizeMappingSourcePool = (pool: Record<string, any> | undefined): MappingSourcePool => ({
  recommended: Array.isArray(pool?.recommended) ? pool!.recommended.map(normalizeSourceOption) : [],
  segmentOutputs: Array.isArray(pool?.segment_outputs) ? pool!.segment_outputs.map(normalizeSourceOption) : [],
  artifacts: Array.isArray(pool?.artifacts) ? pool!.artifacts.map(normalizeSourceOption) : [],
  workflowParams: Array.isArray(pool?.workflow_params) ? pool!.workflow_params.map(normalizeSourceOption) : [],
})

const normalizeSourceOption = (item: Record<string, any>) => ({
  id: String(item.id || ''),
  sourceType: item.source_type,
  sourceRef: String(item.source_ref || ''),
  segmentId: item.segment_id ? String(item.segment_id) : undefined,
  segmentTitle: item.segment_title ? String(item.segment_title) : undefined,
  name: String(item.name || ''),
  valueType: item.value_type || 'string',
  preview: item.preview ? String(item.preview) : '',
})

export async function getRecordingSegmentMappingSources(
  sessionId: string,
  runId: string,
  segmentId: string,
) {
  const response = await apiClient.get(
    `/sessions/${sessionId}/recordings/${runId}/segments/${segmentId}/mapping-sources`,
  )
  const data = response.data.data
  return {
    runId: String(data.run_id || runId),
    segmentId: String(data.segment_id || segmentId),
    summary: data.summary as RecordingSegmentSummary,
    sourcePool: normalizeMappingSourcePool(data.source_pool),
  }
}

export async function updateRecordingSegmentBindings(
  sessionId: string,
  runId: string,
  segmentId: string,
  inputs: WorkflowIO[],
) {
  const response = await apiClient.put(
    `/sessions/${sessionId}/recordings/${runId}/segments/${segmentId}/bindings`,
    { inputs },
  )
  const data = response.data.data
  return {
    run: data.run,
    segment: data.segment,
    summary: data.summary as RecordingSegmentSummary,
    sourcePool: normalizeMappingSourcePool(data.source_pool),
  }
}

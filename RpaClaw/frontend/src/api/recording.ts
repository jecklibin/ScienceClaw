import { apiClient } from '@/api/client'
import type {
  RecordingArtifact,
  RecordingParamConfig,
  RecordingStep,
  SkillPublishDraft,
} from '@/types/recording'

export async function createRecordingRun(sessionId: string, message: string) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings`, { message })
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

export async function testRecordingRun(sessionId: string, runId: string) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings/${runId}/test`)
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

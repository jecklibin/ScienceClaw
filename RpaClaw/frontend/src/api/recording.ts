import { apiClient } from '@/api/client'
import type { RecordingArtifact, RecordingStep } from '@/types/recording'

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
  publishTarget: 'skill' | 'tool',
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/publish`,
    { publish_target: publishTarget },
  )
  return response.data.data
}

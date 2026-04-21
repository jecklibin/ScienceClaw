export interface RecordingRouteContext {
  runId?: string
  segmentId?: string
}

export function getRecordingRouteContext(route: { query?: Record<string, string> } | null | undefined): RecordingRouteContext {
  return {
    runId: route?.query?.runId,
    segmentId: route?.query?.segmentId,
  }
}

export function matchesRecordingRouteContext(
  route: { query?: Record<string, string> } | null | undefined,
  payload: RecordingRouteContext | null | undefined,
): boolean {
  const current = getRecordingRouteContext(route)
  if (!current.runId && !current.segmentId) {
    return true
  }
  if (!payload) {
    return false
  }
  if (current.runId && payload.runId && current.runId !== payload.runId) {
    return false
  }
  if (current.segmentId && payload.segmentId && current.segmentId !== payload.segmentId) {
    return false
  }
  if (current.runId && !payload.runId) {
    return false
  }
  if (current.segmentId && !payload.segmentId) {
    return false
  }
  return true
}

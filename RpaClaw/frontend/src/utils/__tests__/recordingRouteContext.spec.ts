import { describe, expect, it } from 'vitest'

import { matchesRecordingRouteContext } from '@/utils/recordingRouteContext'

describe('matchesRecordingRouteContext', () => {
  const route = {
    query: {
      runId: 'run-1',
      segmentId: 'seg-2',
    },
  }

  it('accepts payloads from the active segment', () => {
    expect(matchesRecordingRouteContext(route, { runId: 'run-1', segmentId: 'seg-2' })).toBe(true)
  })

  it('rejects stale payloads from older segments', () => {
    expect(matchesRecordingRouteContext(route, { runId: 'run-1', segmentId: 'seg-1' })).toBe(false)
  })

  it('rejects unscoped close payloads when current route is segment-scoped', () => {
    expect(matchesRecordingRouteContext(route, undefined)).toBe(false)
  })
})

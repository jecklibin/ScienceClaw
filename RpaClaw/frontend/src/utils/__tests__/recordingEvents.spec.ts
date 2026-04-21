import { describe, expect, it } from 'vitest'

import { normalizeToolContent } from '@/utils/recordingEvents'

describe('normalizeToolContent', () => {
  it('parses stringified recording lifecycle payloads', () => {
    const payload = normalizeToolContent(JSON.stringify({
      recording_event: 'recording_run_started',
      open_workbench: true,
      run: { id: 'run-1', status: 'recording', type: 'rpa' },
      segment: { id: 'seg-1', status: 'recording', kind: 'rpa' },
    }))

    expect(payload.content).toMatchObject({
      recording_event: 'recording_run_started',
      open_workbench: true,
    })
    expect(payload.recordingPayload?.recording_event).toBe('recording_run_started')
  })

  it('keeps plain text untouched', () => {
    const payload = normalizeToolContent('not-json')

    expect(payload.content).toBe('not-json')
    expect(payload.recordingPayload).toBeNull()
  })

  it('ignores unrelated json payloads', () => {
    const payload = normalizeToolContent('{"ok":true}')

    expect(payload.content).toEqual({ ok: true })
    expect(payload.recordingPayload).toBeNull()
  })
})

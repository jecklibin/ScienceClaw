import { describe, expect, it } from 'vitest'

import { normalizeToolContent } from './recordingEvents'

const lifecyclePayload = {
  recording_event: 'recording_run_started',
  run: { id: 'run-1', status: 'recording' },
  segment: { id: 'seg-2', status: 'recording' },
  open_workbench: true,
}

describe('normalizeToolContent', () => {
  it('extracts recording lifecycle payloads from text content blocks', () => {
    const { recordingPayload } = normalizeToolContent([
      {
        type: 'text',
        text: JSON.stringify(lifecyclePayload),
      },
    ])

    expect(recordingPayload).toMatchObject(lifecyclePayload)
  })

  it('extracts recording lifecycle payloads embedded in fenced text', () => {
    const { recordingPayload } = normalizeToolContent(
      `continued recording:\n\`\`\`json\n${JSON.stringify(lifecyclePayload)}\n\`\`\``,
    )

    expect(recordingPayload).toMatchObject(lifecyclePayload)
  })
})

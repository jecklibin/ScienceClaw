import type {
  RecordingPublishPreparedPayload,
  RecordingRunStartedPayload,
  RecordingSegmentCompletedPayload,
  RecordingSegmentUpdatedPayload,
  RecordingTestStartedPayload,
} from '@/types/recording'

export type RecordingLifecyclePayload =
  | ({ recording_event: 'recording_run_started' } & RecordingRunStartedPayload)
  | ({ recording_event: 'recording_segment_completed' } & RecordingSegmentCompletedPayload)
  | ({ recording_event: 'recording_segment_updated' } & RecordingSegmentUpdatedPayload)
  | ({ recording_event: 'recording_test_started' } & RecordingTestStartedPayload)
  | ({ recording_event: 'recording_publish_prepared' } & RecordingPublishPreparedPayload)

const RECORDING_EVENTS = new Set<string>([
  'recording_run_started',
  'recording_segment_completed',
  'recording_segment_updated',
  'recording_test_started',
  'recording_publish_prepared',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function isRecordingPayload(value: unknown): value is RecordingLifecyclePayload {
  return (
    isRecord(value)
    && 'recording_event' in value
    && RECORDING_EVENTS.has(String(value.recording_event || ''))
  )
}

function parseJson(value: string): unknown | null {
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

function extractJsonSnippets(text: string): string[] {
  const snippets: string[] = []
  const fencedBlockPattern = /```(?:json)?\s*([\s\S]*?)```/gi
  let fencedMatch: RegExpExecArray | null

  while ((fencedMatch = fencedBlockPattern.exec(text))) {
    snippets.push(fencedMatch[1].trim())
  }

  let searchIndex = 0
  while (searchIndex < text.length) {
    const eventIndex = text.indexOf('"recording_event"', searchIndex)
    if (eventIndex < 0) {
      break
    }

    const objectStart = text.lastIndexOf('{', eventIndex)
    if (objectStart < 0) {
      searchIndex = eventIndex + 1
      continue
    }

    let depth = 0
    let inString = false
    let escaped = false

    for (let index = objectStart; index < text.length; index += 1) {
      const char = text[index]

      if (escaped) {
        escaped = false
        continue
      }

      if (char === '\\') {
        escaped = inString
        continue
      }

      if (char === '"') {
        inString = !inString
        continue
      }

      if (inString) {
        continue
      }

      if (char === '{') {
        depth += 1
      } else if (char === '}') {
        depth -= 1
        if (depth === 0) {
          snippets.push(text.slice(objectStart, index + 1))
          searchIndex = index + 1
          break
        }
      }
    }

    if (searchIndex <= eventIndex) {
      searchIndex = eventIndex + 1
    }
  }

  return snippets
}

function extractRecordingPayload(value: unknown): RecordingLifecyclePayload | null {
  if (isRecordingPayload(value)) {
    return value
  }

  if (typeof value === 'string') {
    const parsed = parseJson(value)
    if (parsed !== null) {
      const parsedPayload = extractRecordingPayload(parsed)
      if (parsedPayload) {
        return parsedPayload
      }
    }

    for (const snippet of extractJsonSnippets(value)) {
      const parsedSnippet = parseJson(snippet)
      if (parsedSnippet !== null) {
        const payload = extractRecordingPayload(parsedSnippet)
        if (payload) {
          return payload
        }
      }
    }

    return null
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const payload = extractRecordingPayload(item)
      if (payload) {
        return payload
      }
    }
    return null
  }

  if (isRecord(value)) {
    const textPayload = extractRecordingPayload(value.text)
    if (textPayload) {
      return textPayload
    }

    const contentPayload = extractRecordingPayload(value.content)
    if (contentPayload) {
      return contentPayload
    }
  }

  return null
}

export function normalizeToolContent(rawContent: unknown): {
  content: unknown
  recordingPayload: RecordingLifecyclePayload | null
} {
  let normalizedContent = rawContent

  if (typeof normalizedContent === 'string') {
    const parsed = parseJson(normalizedContent)
    if (parsed !== null) {
      normalizedContent = parsed
    }
  }

  return {
    content: normalizedContent,
    recordingPayload: extractRecordingPayload(normalizedContent),
  }
}

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

export function normalizeToolContent(rawContent: unknown): {
  content: unknown
  recordingPayload: RecordingLifecyclePayload | null
} {
  let normalizedContent = rawContent

  if (typeof normalizedContent === 'string') {
    try {
      normalizedContent = JSON.parse(normalizedContent)
    } catch {
      return {
        content: rawContent,
        recordingPayload: null,
      }
    }
  }

  if (
    normalizedContent
    && typeof normalizedContent === 'object'
    && 'recording_event' in normalizedContent
    && RECORDING_EVENTS.has(String((normalizedContent as { recording_event?: unknown }).recording_event || ''))
  ) {
    return {
      content: normalizedContent,
      recordingPayload: normalizedContent as RecordingLifecyclePayload,
    }
  }

  return {
    content: normalizedContent,
    recordingPayload: null,
  }
}

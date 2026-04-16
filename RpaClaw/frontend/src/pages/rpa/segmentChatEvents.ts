export interface ChatAction {
  description: string;
  code: string;
  showCode?: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  time: string;
  script?: string;
  status?: 'streaming' | 'executing' | 'done' | 'error';
  error?: string;
  showCode?: boolean;
  actions?: ChatAction[];
  frameSummary?: string;
  locatorSummary?: string;
  collectionSummary?: string;
  diagnostics?: string[];
}

type SegmentEventType =
  | 'segment_planned'
  | 'segment_started'
  | 'segment_reobserved'
  | 'segment_validation_failed'
  | 'segment_recovering'
  | 'recording_aborted'
  | 'error';

interface RecordedAiStep {
  id: string;
  title: string;
  description: string;
  status: 'completed';
  source: 'ai';
  sensitive: boolean;
  frameSummary?: string;
  locatorSummary?: string;
  diagnostics?: string[];
}

interface SegmentEventData {
  thought?: string;
  segment_goal?: string;
  segment_kind?: string;
  stop_reason?: string;
  error_code?: string;
  message?: string;
  reason?: string;
  attempt?: number;
  url?: string;
  title?: string;
  page_changed?: boolean;
}

interface SegmentStepData {
  action?: string;
  source?: string;
  description?: string;
  prompt?: string;
  sensitive?: boolean;
  frame_path?: string[];
  assistant_diagnostics?: {
    execution_mode?: string;
    upgrade_reason?: string;
    recovery_attempts?: number;
    segment_kind?: string;
    stop_reason?: string;
  };
}

const appendDiagnostics = (message: ChatMessage, diagnostics: Array<string | undefined>): ChatMessage => {
  const nextDiagnostics = [...(message.diagnostics ?? [])];
  for (const diagnostic of diagnostics) {
    if (diagnostic) nextDiagnostics.push(diagnostic);
  }
  return {
    ...message,
    diagnostics: nextDiagnostics.length > 0 ? nextDiagnostics : undefined,
  };
};

const formatFramePath = (framePath?: string[]) => {
  if (!framePath || framePath.length === 0) return undefined;
  return framePath.join(' -> ');
};

export const createAssistantMessage = (time: string): ChatMessage => ({
  role: 'assistant',
  text: '',
  time,
  status: 'streaming',
});

export const applySegmentEvent = (
  message: ChatMessage,
  eventType: SegmentEventType,
  data: SegmentEventData,
): ChatMessage => {
  switch (eventType) {
    case 'segment_planned':
      return appendDiagnostics(
        {
          ...message,
          status: message.status === 'error' ? message.status : 'executing',
          error: undefined,
        },
        [
          data.thought ? `Plan: ${data.thought}` : undefined,
          data.segment_goal ? `Segment: ${data.segment_goal}` : undefined,
          data.segment_kind ? `Kind: ${data.segment_kind}` : undefined,
          data.stop_reason ? `Stop: ${data.stop_reason}` : undefined,
        ],
      );
    case 'segment_started':
      return appendDiagnostics(
        {
          ...message,
          status: 'executing',
          error: undefined,
        },
        [data.segment_goal ? `Running: ${data.segment_goal}` : 'Running segment'],
      );
    case 'segment_reobserved':
      return appendDiagnostics(
        {
          ...message,
          status: 'executing',
          error: undefined,
        },
        [
          data.segment_goal ? `Reobserve: ${data.segment_goal}` : undefined,
          typeof data.page_changed === 'boolean' ? `Page changed: ${data.page_changed}` : undefined,
          data.url ? `URL: ${data.url}` : undefined,
          data.title ? `Title: ${data.title}` : undefined,
        ],
      );
    case 'segment_validation_failed':
      return appendDiagnostics(
        {
          ...message,
          status: 'executing',
          error: undefined,
        },
        [
          data.segment_goal ? `Validation failed: ${data.segment_goal}` : 'Validation failed',
          data.reason,
        ],
      );
    case 'segment_recovering':
      return appendDiagnostics(
        {
          ...message,
          status: 'executing',
          error: undefined,
        },
        [
          `Recovery ${data.attempt ?? '?'}: ${data.segment_goal || 'Segment is retrying'}`,
          data.error_code ? `Error code: ${data.error_code}` : undefined,
          data.message,
        ],
      );
    case 'recording_aborted': {
      const nextError = data.reason || data.message || 'Recording aborted';
      return appendDiagnostics(
        {
          ...message,
          status: 'error',
          error: nextError,
        },
        [
          data.segment_goal ? `Aborted: ${data.segment_goal}` : undefined,
          data.error_code ? `Error code: ${data.error_code}` : undefined,
          data.message && data.message !== nextError ? data.message : undefined,
        ],
      );
    }
    case 'error': {
      const nextError = data.message || 'Unknown error';
      return appendDiagnostics(
        {
          ...message,
          status: 'error',
          error: nextError,
        },
        [nextError],
      );
    }
  }
};

export const toRecordedAiStep = (step: SegmentStepData, index: number): RecordedAiStep => {
  const diagnostics: string[] = [];

  if (step.assistant_diagnostics?.segment_kind) {
    diagnostics.push(`Segment kind: ${step.assistant_diagnostics.segment_kind}`);
  }
  if (step.assistant_diagnostics?.stop_reason) {
    diagnostics.push(`Stop reason: ${step.assistant_diagnostics.stop_reason}`);
  }
  if (typeof step.assistant_diagnostics?.recovery_attempts === 'number') {
    diagnostics.push(`Recovery attempts: ${step.assistant_diagnostics.recovery_attempts}`);
  }

  return {
    id: String(index),
    title: step.description || step.action || 'AI segment',
    description: step.prompt || step.description || 'AI segment',
    status: 'completed',
    source: 'ai',
    sensitive: Boolean(step.sensitive),
    frameSummary: formatFramePath(step.frame_path),
    diagnostics: diagnostics.length > 0 ? diagnostics : undefined,
  };
};

export const toPolledRecordedAiStep = (
  step: SegmentStepData,
  index: number,
  locatorSummary?: string,
): RecordedAiStep => ({
  ...toRecordedAiStep(step, index),
  locatorSummary,
});

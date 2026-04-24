export interface RpaConfigureStep {
  id: string;
  action: string;
  target?: any;
  frame_path?: string[];
  locator_candidates?: any[];
  validation?: {
    status?: string;
    details?: string;
  };
  value?: string;
  description?: string;
  label?: string;
  sensitive?: boolean;
  url?: string;
  source?: string;
  configurable?: boolean;
  stepId?: string;
}

export interface RpaRecordingDiagnosticItem {
  id: string;
  stepId: string;
  stepIndex: number | null;
  action: string;
  description: string;
  failureReason: string;
  locator_candidates: any[];
  validation: {
    status: string;
    details: string;
  };
  url?: string;
  source: 'record';
  configurable: boolean;
}

const TRACE_LABELS: Record<string, string> = {
  ai_operation: 'AI Trace',
  data_capture: 'Data Capture',
  dataflow_fill: 'Dataflow Fill',
  navigation: 'Navigation',
  manual_action: 'Manual',
};

export const formatRpaTraceType = (traceType?: string) => {
  if (!traceType) return 'Trace';
  return TRACE_LABELS[traceType] || traceType;
};

const formatDiagnosticReason = (reason?: string) => {
  if (!reason) return 'Unresolved diagnostic';
  return reason.replace(/_/g, ' ');
};

const firstLocatorCandidate = (trace: any) => {
  const candidates = Array.isArray(trace?.locator_candidates) ? trace.locator_candidates : [];
  if (candidates.length === 0) return null;
  const selected = candidates.find((candidate: any) => candidate?.selected) || candidates[0];
  return selected?.locator || selected || null;
};

const normalizeCandidates = (candidates: any[], fallbackKind: string) => (
  (Array.isArray(candidates) ? candidates : []).map((candidate: any, index: number) => ({
    kind: candidate?.kind || fallbackKind,
    score: candidate?.score,
    selected: candidate?.selected ?? index === 0,
    reason: candidate?.reason,
    strict_match_count: candidate?.strict_match_count,
    visible_match_count: candidate?.visible_match_count,
    locator: candidate?.locator || candidate,
    playwright_locator: candidate?.playwright_locator,
    selector: candidate?.selector,
  }))
);

const normalizeTraceCandidates = (trace: any) => (
  normalizeCandidates(trace?.locator_candidates, formatRpaTraceType(trace?.trace_type))
);

const buildAcceptedActionCandidates = (action: any) => {
  const candidates = normalizeCandidates(action?.raw_candidates, action?.action_kind || 'record');
  if (candidates.length > 0) {
    return candidates;
  }
  if (action?.target) {
    return [{
      kind: action?.action_kind || 'record',
      selected: true,
      locator: action.target,
    }];
  }
  return [];
};

const traceAction = (trace: any) => {
  if (trace?.trace_type === 'navigation') return 'navigate';
  if (trace?.trace_type === 'manual_action') return trace?.action || 'manual_action';
  return trace?.trace_type || trace?.action || 'trace';
};

const manualActionSourceLabel = (action: any) => (
  action?.validation?.details || 'Accepted manual action'
);

const mapRecordedActions = (session: any): RpaConfigureStep[] => {
  const actions = Array.isArray(session?.recorded_actions) ? session.recorded_actions : [];
  return actions.map((action: any, index: number) => ({
    id: String(action?.step_id || `recorded-action-${index}`),
    stepId: String(action?.step_id || ''),
    action: action?.action_kind || 'record',
    target: action?.target || null,
    locator_candidates: buildAcceptedActionCandidates(action),
    validation: {
      status: action?.validation?.status || 'ok',
      details: manualActionSourceLabel(action),
    },
    value: action?.value,
    description: action?.description || action?.action_kind || 'Accepted manual action',
    label: action?.action_kind || 'record',
    sensitive: false,
    url: action?.page_state?.url || '',
    source: 'record',
    configurable: false,
  }));
};

export const mapRpaConfigureDisplaySteps = (session: any): RpaConfigureStep[] => {
  const recordedActions = Array.isArray(session?.recorded_actions) ? session.recorded_actions : [];
  if (recordedActions.length > 0) {
    return mapRecordedActions(session);
  }

  const traces = Array.isArray(session?.traces) ? session.traces : [];
  if (traces.length === 0) {
    return getLegacyRpaSteps(session);
  }

  return traces.map((trace: any, index: number) => {
    const traceTypeLabel = formatRpaTraceType(trace?.trace_type);
    const locator = firstLocatorCandidate(trace);
    const afterUrl = trace?.after_page?.url || '';
    return {
      id: String(trace?.trace_id || `trace-${index}`),
      action: traceAction(trace),
      target: locator,
      locator_candidates: normalizeTraceCandidates(trace),
      validation: {
        status: trace?.accepted === false ? 'warning' : 'ok',
        details: traceTypeLabel,
      },
      value: trace?.value,
      description: trace?.description || trace?.user_instruction || traceTypeLabel,
      label: trace?.user_instruction || trace?.action || traceTypeLabel,
      sensitive: false,
      url: afterUrl,
      source: trace?.source === 'ai' || trace?.trace_type === 'ai_operation' ? 'ai' : 'record',
      configurable: false,
    };
  });
};

export const getLegacyRpaSteps = (session: any): RpaConfigureStep[] => (
  Array.isArray(session?.steps) ? session.steps : []
);

export const getManualRecordingDiagnostics = (session: any): RpaRecordingDiagnosticItem[] => {
  const diagnostics = Array.isArray(session?.recording_diagnostics) ? session.recording_diagnostics : [];
  const legacySteps = getLegacyRpaSteps(session);
  const stepIndexes = new Map<string, number>();
  legacySteps.forEach((step, index) => {
    if (step?.id) {
      stepIndexes.set(String(step.id), index);
    }
  });

  return diagnostics.map((diagnostic: any, index: number) => {
    const stepId = String(diagnostic?.related_step_id || '');
    const relatedStep = legacySteps.find((step) => String(step?.id || '') === stepId);
    const stepIndex = stepId && stepIndexes.has(stepId) ? stepIndexes.get(stepId)! : null;
    const action = diagnostic?.related_action_kind || relatedStep?.action || 'record';
    const detail = formatDiagnosticReason(diagnostic?.failure_reason);
    return {
      id: `diagnostic-${stepId || index}`,
      stepId,
      stepIndex,
      action,
      description: relatedStep?.description || `${action} requires repair`,
      failureReason: diagnostic?.failure_reason || 'manual_recording_unresolved',
      locator_candidates: normalizeCandidates(
        diagnostic?.raw_candidates || relatedStep?.locator_candidates || [],
        action,
      ),
      validation: {
        status: 'broken',
        details: detail,
      },
      url: diagnostic?.page_state?.url || relatedStep?.url || '',
      source: 'record',
      configurable: stepIndex !== null,
    };
  });
};

export const hasManualRecordingDiagnostics = (session: any): boolean => (
  getManualRecordingDiagnostics(session).length > 0
);

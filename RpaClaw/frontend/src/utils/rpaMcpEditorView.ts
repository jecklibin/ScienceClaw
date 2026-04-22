import type { JsonSchemaObject } from '@/api/rpaMcp';
import type { RecordingStep, WorkflowIO, WorkflowValueType } from '@/types/recording';

export function countSchemaProperties(schema: unknown): number {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return 0;
  const properties = (schema as { properties?: unknown }).properties;
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return 0;
  return Object.keys(properties as Record<string, unknown>).length;
}

export function buildRecordedStepSummary(steps: Array<Record<string, any>>) {
  const total = steps.length;
  const strict = steps.filter((step) => step?.validation?.status === 'ok').length;
  const needsAttention = steps.filter((step) => {
    const status = step?.validation?.status;
    return status === 'warning' || status === 'fallback' || status === 'ambiguous' || status === 'broken';
  }).length;
  const withCandidates = steps.filter((step) => Array.isArray(step?.locator_candidates) && step.locator_candidates.length > 0).length;

  return {
    total,
    strict,
    needsAttention,
    withCandidates,
  };
}

export function buildSchemaSummary(preview: { input_schema?: unknown; output_schema?: unknown }) {
  return {
    inputFields: countSchemaProperties(preview.input_schema),
    outputFields: countSchemaProperties(preview.output_schema),
  };
}

export function shouldShowCookieSection(
  preview: { requires_cookies?: boolean } | null | undefined,
  cookieSectionOpen: boolean,
): boolean {
  if (!preview) return false;
  return Boolean(preview.requires_cookies || cookieSectionOpen);
}

function normalizeSchemaType(rawType: unknown): WorkflowValueType {
  const type = Array.isArray(rawType)
    ? rawType.find((item) => typeof item === 'string' && item !== 'null')
    : rawType;
  if (type === 'number' || type === 'integer') return 'number';
  if (type === 'boolean') return 'boolean';
  if (type === 'array' || type === 'object') return 'json';
  return 'string';
}

function getSchemaProperties(schema: unknown): Record<string, JsonSchemaObject> {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return {};
  const properties = (schema as { properties?: unknown }).properties;
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return {};
  return properties as Record<string, JsonSchemaObject>;
}

function getSchemaRequired(schema: unknown): Set<string> {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return new Set();
  const required = (schema as { required?: unknown }).required;
  return new Set(Array.isArray(required) ? required.filter((item): item is string => typeof item === 'string') : []);
}

export function buildWorkflowInputsFromSchema(schema: JsonSchemaObject | null | undefined): WorkflowIO[] {
  const properties = getSchemaProperties(schema);
  const required = getSchemaRequired(schema);
  return Object.entries(properties)
    .filter(([name]) => name !== 'cookies')
    .map(([name, prop]) => ({
      name,
      type: normalizeSchemaType(prop?.type),
      required: required.has(name),
      source: 'user',
      description: typeof prop?.description === 'string' ? prop.description : '',
      default: prop?.default,
    }));
}

export function buildWorkflowOutputsFromSchema(schema: JsonSchemaObject | null | undefined): WorkflowIO[] {
  const rootProperties = getSchemaProperties(schema);
  const dataSchema = rootProperties.data;
  const dataProperties = getSchemaProperties(dataSchema);
  const outputProperties = Object.keys(dataProperties).length
    ? dataProperties
    : Object.fromEntries(
        Object.entries(rootProperties)
          .filter(([name]) => !['success', 'message', 'downloads', 'artifacts', 'error'].includes(name)),
      );
  const required = Object.keys(dataProperties).length ? getSchemaRequired(dataSchema) : getSchemaRequired(schema);
  return Object.entries(outputProperties).map(([name, prop]) => ({
    name,
    type: normalizeSchemaType(prop?.type),
    required: required.has(name),
    description: typeof prop?.description === 'string' ? prop.description : '',
    source: 'segment_output',
  }));
}

export function buildRecordingStepsFromMcpEditorSteps(steps: Array<Record<string, any>>): RecordingStep[] {
  return steps.map((step, index) => {
    const rawTarget = step.target ?? step.label ?? '';
    const target = typeof rawTarget === 'string' ? rawTarget : JSON.stringify(rawTarget);
    return {
      id: String(step.id || `step-${index + 1}`),
      step_index: index,
      action: String(step.action || 'unknown'),
      description: typeof step.description === 'string' ? step.description : undefined,
      target,
      value: step.value,
      url: typeof step.url === 'string' ? step.url : null,
      frame_path: Array.isArray(step.frame_path) ? step.frame_path : undefined,
      result_key: typeof step.result_key === 'string' ? step.result_key : undefined,
      validation: step.validation,
      locator_candidates: step.locator_candidates,
      signals: step.signals,
      element_snapshot: step.element_snapshot,
      tag: typeof step.tag === 'string' ? step.tag : null,
      label: typeof step.label === 'string' ? step.label : null,
      sensitive: Boolean(step.sensitive),
      tab_id: typeof step.tab_id === 'string' ? step.tab_id : null,
      source_tab_id: typeof step.source_tab_id === 'string' ? step.source_tab_id : null,
      target_tab_id: typeof step.target_tab_id === 'string' ? step.target_tab_id : null,
      sequence: typeof step.sequence === 'number' ? step.sequence : null,
      event_timestamp_ms: typeof step.event_timestamp_ms === 'number' ? step.event_timestamp_ms : null,
    };
  });
}

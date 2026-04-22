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

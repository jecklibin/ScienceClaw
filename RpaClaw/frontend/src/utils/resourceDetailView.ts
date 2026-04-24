export function buildResourceSummaryCounts(input: {
  params?: Record<string, unknown> | Array<unknown> | null;
  steps?: Array<Record<string, unknown>> | null;
  files?: Array<{ path: string }> | null;
}) {
  const paramCount = Array.isArray(input.params)
    ? input.params.length
    : Object.keys(input.params || {}).length;

  return {
    params: paramCount,
    steps: input.steps?.length || 0,
    files: input.files?.length || 0,
  };
}

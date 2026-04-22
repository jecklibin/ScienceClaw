import type {
  InputBindingSummary,
  MappingSourceOption,
  MappingSourcePool,
  RecordingPublishPreparedPayload,
  RecordingArtifact,
  RecordingSegmentSummary,
  RecordingStep,
  WorkflowIO,
} from '@/types/recording'

export interface RpaStep {
  id: string
  action: string
  description?: string
  target?: string
  result_key?: string
  value?: string | null
  url?: string | null
  frame_path?: string[]
  validation?: RecordingStep['validation']
  locator_candidates?: RecordingStep['locator_candidates']
  signals?: Record<string, any>
  element_snapshot?: Record<string, unknown>
  tag?: string | null
  label?: string | null
  sensitive?: boolean
  tab_id?: string | null
  source_tab_id?: string | null
  target_tab_id?: string | null
  sequence?: number | null
  event_timestamp_ms?: number | null
}

export function mapRpaStepsToRecordingSteps(steps: RpaStep[]): RecordingStep[] {
  return steps.map((step, index) => ({
    id: step.id,
    step_index: index,
    action: step.action,
    description: step.description,
    target: step.target,
    value: step.value,
    url: step.url,
    frame_path: step.frame_path,
    result_key: step.result_key,
    validation: step.validation,
    locator_candidates: step.locator_candidates,
    signals: step.signals,
    element_snapshot: step.element_snapshot,
    tag: step.tag,
    label: step.label,
    sensitive: step.sensitive,
    tab_id: step.tab_id,
    source_tab_id: step.source_tab_id,
    target_tab_id: step.target_tab_id,
    sequence: step.sequence,
    event_timestamp_ms: step.event_timestamp_ms,
  }))
}

export function deriveArtifactsFromRpaSteps(steps: RpaStep[]): RecordingArtifact[] {
  const artifacts: RecordingArtifact[] = []
  for (const step of steps) {
    const downloadSignal = step.signals?.download
    if (downloadSignal?.path) {
      artifacts.push({
        name: downloadSignal.filename || step.value || `download_${step.id}`,
        type: 'file',
        path: downloadSignal.path,
        labels: ['recording', 'download'],
      })
    }
    if (step.result_key && step.value) {
      artifacts.push({
        name: step.result_key,
        type: 'text',
        value: step.value,
        labels: ['recording', 'extracted'],
      })
    }
  }
  return artifacts
}

export function deriveSummaryInputs(summary: RecordingSegmentSummary): WorkflowIO[] {
  if (Array.isArray(summary.inputs) && summary.inputs.length) {
    return summary.inputs
  }

  const params = summary.params || {}
  return Object.entries(params)
    .filter(([name]) => !!name)
    .map(([name, config]) => {
      const sensitive = !!config?.sensitive
      return {
        name,
        type: sensitive ? 'secret' : 'string',
        required: false,
        source: sensitive ? 'credential' : 'user',
        description: `Segment param ${name}`,
        default: sensitive ? undefined : config?.original_value,
      } satisfies WorkflowIO
    })
}

export function deriveSummaryOutputs(summary: RecordingSegmentSummary): WorkflowIO[] {
  if (Array.isArray(summary.outputs) && summary.outputs.length) {
    return summary.outputs
  }

  const outputs: WorkflowIO[] = []
  const seenNames = new Set<string>()

  for (const step of summary.steps || []) {
    if (step.action !== 'extract_text' || !step.result_key || seenNames.has(step.result_key)) {
      continue
    }
    seenNames.add(step.result_key)
    outputs.push({
      name: step.result_key,
      type: 'string',
      description: step.description || `Extracted value ${step.result_key}`,
    })
  }

  for (const artifact of summary.artifacts || []) {
    if (!artifact.name || seenNames.has(artifact.name)) {
      continue
    }
    seenNames.add(artifact.name)
    outputs.push({
      name: artifact.name,
      type: artifact.type === 'file' ? 'file' : 'json',
      description: `Artifact ${artifact.name}`,
    })
  }

  return outputs
}

export function buildMappingSourcePool(args: {
  currentSegmentId: string
  summaries: RecordingSegmentSummary[]
  workflowParams: WorkflowIO[]
}): MappingSourcePool {
  const historical = args.summaries.filter((item) => item.segment_id !== args.currentSegmentId)
  const segmentOutputs = historical.flatMap((summary) =>
    deriveSummaryOutputs(summary)
      .filter((output) => !(summary.artifacts || []).some((artifact) => artifact.name === output.name))
      .map((output): MappingSourceOption => ({
      id: `${summary.segment_id}:${output.name}`,
      sourceType: 'segment_output',
      sourceRef: `${summary.segment_id}.outputs.${output.name}`,
      segmentId: summary.segment_id,
      segmentTitle: summary.title || summary.intent || summary.segment_id,
      name: output.name,
      valueType: output.type,
      preview: output.description || '',
    })),
  )
  const artifacts = historical.flatMap((summary) =>
    (summary.artifacts || []).map((artifact): MappingSourceOption => ({
      id: artifact.id || `${summary.segment_id}:${artifact.name}`,
      sourceType: 'artifact',
      sourceRef: artifact.id ? `artifact:${artifact.id}` : `artifact:${artifact.name}`,
      segmentId: summary.segment_id,
      segmentTitle: summary.title || summary.intent || summary.segment_id,
      name: artifact.name,
      valueType: artifact.type === 'file' ? 'file' : artifact.type === 'text' ? 'string' : 'json',
      preview: artifact.path || (artifact.value === undefined || artifact.value === null ? '' : String(artifact.value)),
    })),
  )

  return {
    recommended: [...segmentOutputs.slice(-3), ...artifacts.slice(-3)].slice(-4).reverse(),
    segmentOutputs,
    artifacts,
    workflowParams: args.workflowParams.map((item): MappingSourceOption => ({
      id: `workflow:${item.name}`,
      sourceType: 'workflow_param',
      sourceRef: `workflow.params.${item.name}`,
      name: item.name,
      valueType: item.type,
      preview: item.description || '',
    })),
  }
}

export function summarizeInputBindings(inputs: WorkflowIO[]): InputBindingSummary {
  const bound = inputs.filter((item) => !!item.source && !!item.source_ref)
  const unbound = inputs.filter((item) => !item.source_ref)
  return {
    boundCount: bound.length,
    unboundCount: unbound.length,
    lines: bound.slice(0, 2).map((item) => `${item.name} <- ${item.source_ref}`),
  }
}

export function derivePublishSaveTarget(
  payload: Partial<RecordingPublishPreparedPayload> | null | undefined,
  options: { includeDraft?: boolean } = {},
): { kind: 'skill' | 'tool'; name: string } | null {
  if (!payload?.prompt_kind) {
    return null
  }
  const kind = payload.prompt_kind === 'skill' ? 'skill' : payload.prompt_kind === 'tool' ? 'tool' : null
  if (!kind) {
    return null
  }
  const summary = payload.summary || {}
  if (!options.includeDraft && summary.draft) {
    return null
  }
  const rawName = typeof summary.name === 'string' && summary.name
    ? summary.name
    : typeof summary.title === 'string' && summary.title
      ? summary.title
      : typeof summary.draft?.skill_name === 'string' && summary.draft.skill_name
        ? summary.draft.skill_name
        : ''
  return rawName ? { kind, name: rawName } : null
}

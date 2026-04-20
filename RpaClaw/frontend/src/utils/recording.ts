import type { RecordingArtifact, RecordingStep } from '@/types/recording'

export interface RpaStep {
  id: string
  action: string
  description?: string
  target?: string
  result_key?: string
  value?: string | null
  url?: string | null
  validation?: RecordingStep['validation']
  locator_candidates?: RecordingStep['locator_candidates']
  signals?: {
    download?: {
      filename?: string
      path?: string
    }
  }
}

export function mapRpaStepsToRecordingSteps(steps: RpaStep[]): RecordingStep[] {
  return steps.map((step, index) => ({
    id: step.id,
    step_index: index,
    action: step.action,
    description: step.description,
    target: step.target,
    validation: step.validation,
    locator_candidates: step.locator_candidates,
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

import { computed, ref } from 'vue'

export interface RecorderStepView {
  id?: string
  title?: string
  description?: string
  action?: string
  status?: string
  locatorSummary?: string
  validationStatus?: string
  validationDetails?: string
}

export function useRecorderSteps(initialSteps: RecorderStepView[] = []) {
  const steps = ref<RecorderStepView[]>(initialSteps)
  const stepCount = computed(() => steps.value.length)

  return {
    steps,
    stepCount,
  }
}

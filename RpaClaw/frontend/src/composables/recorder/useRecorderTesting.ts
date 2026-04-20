import { ref } from 'vue'

export function useRecorderTesting() {
  const testingState = ref({
    status: 'idle' as 'idle' | 'running' | 'failed' | 'passed',
    failed_step_index: null as number | null,
    error: '',
  })

  return {
    testingState,
  }
}

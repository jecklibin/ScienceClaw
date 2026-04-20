<template>
  <div class="mb-3 rounded-2xl border border-emerald-200/70 bg-emerald-50/70 p-4 shadow-sm dark:border-emerald-900/60 dark:bg-emerald-950/20">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ summary.intent || '已完成录制段' }}</div>
        <div class="mt-1 text-xs text-emerald-700 dark:text-emerald-300">
          {{ summary.kind || 'rpa' }} · {{ summary.status || 'completed' }}
        </div>
      </div>
      <div class="rounded-full bg-white/80 px-2.5 py-1 text-xs font-medium text-emerald-700 shadow-sm dark:bg-gray-900/80 dark:text-emerald-300">
        Segment
      </div>
    </div>

    <div class="mt-3 text-xs text-gray-600 dark:text-gray-300">
      产物数：{{ summary.artifacts.length }}
    </div>

    <div v-if="steps.length" class="mt-4 space-y-3">
      <div
        v-for="(step, index) in steps"
        :key="step.id"
        class="rounded-xl border border-white/80 bg-white/70 p-3 dark:border-gray-800 dark:bg-gray-950/40"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="text-sm font-medium text-gray-900 dark:text-gray-100">
              {{ index + 1 }}. {{ step.description || step.action }}
            </div>
            <div class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
              {{ step.target || '未生成定位器' }}
            </div>
          </div>
          <span
            class="rounded-full px-2 py-0.5 text-[11px] font-medium"
            :class="statusClass(step.validation?.status)"
          >
            {{ step.validation?.status || 'unknown' }}
          </span>
        </div>

        <div v-if="step.locator_candidates?.length" class="mt-3">
          <div class="text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
            候选定位器
          </div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-for="(candidate, candidateIndex) in step.locator_candidates"
              :key="`${step.id}-${candidateIndex}`"
              class="rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="candidate.selected
                ? 'border-emerald-500 bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800'"
              :disabled="isRepairing(step.step_index)"
              @click="switchLocator(step.step_index, candidateIndex)"
            >
              {{ candidate.kind || `候选 ${candidateIndex + 1}` }}
            </button>
          </div>
          <div v-if="repairErrors[step.id]" class="mt-2 text-xs text-rose-600 dark:text-rose-300">
            {{ repairErrors[step.id] }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import { promoteRecordingStepLocator } from '@/api/recording'
import type { RecordingSegmentSummary, RecordingStep } from '@/types/recording'

const props = defineProps<{
  summary: RecordingSegmentSummary
}>()

const steps = ref<RecordingStep[]>(props.summary.steps ? [...props.summary.steps] : [])
const repairingStepIndex = ref<number | null>(null)
const repairErrors = ref<Record<string, string>>({})

watch(
  () => props.summary.steps,
  (nextSteps) => {
    steps.value = nextSteps ? [...nextSteps] : []
    repairErrors.value = {}
  },
  { immediate: true },
)

const statusClass = (status?: string) => {
  if (status === 'ok') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
  if (status === 'fallback' || status === 'ambiguous') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
  if (status === 'broken') return 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300'
  return 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
}

const isRepairing = (stepIndex?: number) => stepIndex !== undefined && repairingStepIndex.value === stepIndex

const switchLocator = async (stepIndex: number | undefined, candidateIndex: number) => {
  if (stepIndex === undefined || !props.summary.session_id) return

  const currentStep = steps.value.find((item) => item.step_index === stepIndex)
  if (!currentStep) return

  repairingStepIndex.value = stepIndex
  repairErrors.value = { ...repairErrors.value, [currentStep.id]: '' }

  try {
    const nextStep = await promoteRecordingStepLocator(props.summary.session_id, stepIndex, candidateIndex)
    steps.value = steps.value.map((item) =>
      item.step_index === stepIndex
        ? {
            ...item,
            ...nextStep,
            step_index: stepIndex,
          }
        : item,
    )
  } catch (error) {
    const message = error instanceof Error ? error.message : '定位器切换失败'
    repairErrors.value = { ...repairErrors.value, [currentStep.id]: message }
  } finally {
    repairingStepIndex.value = null
  }
}
</script>

<template>
  <div class="mb-3 overflow-hidden rounded-3xl border border-violet-100/80 bg-white shadow-[0_18px_60px_-36px_rgba(76,29,149,0.55)] dark:border-violet-900/50 dark:bg-gray-950/70">
    <div class="relative p-4 sm:p-5">
      <div class="pointer-events-none absolute -right-12 -top-16 h-40 w-40 rounded-full bg-violet-200/30 blur-3xl dark:bg-violet-800/20" />
      <div class="relative flex items-start gap-4">
        <div class="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/20">
          <span class="text-lg font-black">R</span>
        </div>

        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="text-[11px] font-bold uppercase tracking-[0.22em] text-violet-500">Recording captured</p>
              <h3 class="mt-1 truncate text-base font-extrabold text-gray-950 dark:text-gray-50">
                {{ summary.title || summary.intent || '已完成录制段' }}
              </h3>
              <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {{ summary.description || `${summary.kind || 'rpa'} · ${summary.status || 'completed'}` }}
              </p>
            </div>
            <span class="rounded-full bg-violet-50 px-3 py-1 text-[11px] font-bold text-violet-700 dark:bg-violet-950/50 dark:text-violet-200">
              Segment
            </span>
          </div>

          <div class="mt-4 grid grid-cols-3 gap-2">
            <div class="rounded-2xl bg-gray-50 p-3 dark:bg-gray-900/70">
              <div class="text-lg font-black text-gray-950 dark:text-gray-50">{{ stepCount }}</div>
              <div class="text-[10px] font-bold uppercase tracking-wider text-gray-500">steps</div>
            </div>
            <div class="rounded-2xl bg-gray-50 p-3 dark:bg-gray-900/70">
              <div class="text-lg font-black text-gray-950 dark:text-gray-50">{{ paramCount }}</div>
              <div class="text-[10px] font-bold uppercase tracking-wider text-gray-500">params</div>
            </div>
            <div class="rounded-2xl bg-gray-50 p-3 dark:bg-gray-900/70">
              <div class="text-lg font-black text-emerald-600">{{ tested ? 'ok' : okStepCount }}</div>
              <div class="text-[10px] font-bold uppercase tracking-wider text-gray-500">{{ tested ? 'tested' : 'stable' }}</div>
            </div>
          </div>

          <div class="mt-4 flex flex-wrap gap-2">
            <button
              data-testid="recording-segment-toggle"
              class="inline-flex items-center justify-center rounded-xl bg-violet-50 px-3 py-2 text-xs font-bold text-violet-700 transition hover:bg-violet-100 dark:bg-violet-950/50 dark:text-violet-200 dark:hover:bg-violet-900/60"
              type="button"
              @click="expanded = !expanded"
            >
              {{ expanded ? '收起步骤' : '查看步骤' }}
            </button>
            <span class="inline-flex items-center rounded-xl bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500 dark:bg-gray-900/70 dark:text-gray-400">
              {{ stepCount }} steps · {{ paramCount }} params
            </span>
            <span
              v-if="authReady"
              class="inline-flex items-center rounded-xl bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
            >
              auth ready
            </span>
            <span
              v-if="tested"
              class="inline-flex items-center rounded-xl bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-700 dark:bg-sky-950/40 dark:text-sky-300"
            >
              tested
            </span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="expanded && steps.length" class="border-t border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-900/40">
      <div class="space-y-3">
        <div
          v-for="(step, index) in steps"
          :key="step.id"
          class="rounded-2xl border border-white bg-white/90 p-3 dark:border-gray-800 dark:bg-gray-950/70"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-sm font-bold text-gray-950 dark:text-gray-50">
                {{ index + 1 }}. {{ step.description || step.action }}
              </div>
              <div class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
                {{ step.target || '未生成定位器' }}
              </div>
            </div>
            <span
              class="rounded-full px-2 py-0.5 text-[11px] font-bold"
              :class="statusClass(step.validation?.status)"
            >
              {{ step.validation?.status || 'unknown' }}
            </span>
          </div>

          <div v-if="step.locator_candidates?.length" class="mt-3">
            <div class="text-[11px] font-bold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              候选定位器
            </div>
            <div class="mt-2 flex flex-wrap gap-2">
              <button
                v-for="(candidate, candidateIndex) in step.locator_candidates"
                :key="`${step.id}-${candidateIndex}`"
                class="rounded-lg border px-2.5 py-1 text-xs font-bold transition-colors"
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
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { promoteRecordingStepLocator } from '@/api/recording'
import type { RecordingSegmentSummary, RecordingStep } from '@/types/recording'

const props = defineProps<{
  summary: RecordingSegmentSummary
}>()

const expanded = ref(false)
const steps = ref<RecordingStep[]>(props.summary.steps ? [...props.summary.steps] : [])
const repairingStepIndex = ref<number | null>(null)
const repairErrors = ref<Record<string, string>>({})

const stepCount = computed(() => steps.value.length)
const paramCount = computed(() => Object.keys(props.summary.params || {}).length)
const authReady = computed(() => {
  const credentialIds = props.summary.auth_config?.credential_ids
  return Array.isArray(credentialIds) ? credentialIds.length > 0 : false
})
const tested = computed(() => props.summary.testing_status === 'passed')
const okStepCount = computed(() =>
  steps.value.filter((step) => step.validation?.status === 'ok').length,
)

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

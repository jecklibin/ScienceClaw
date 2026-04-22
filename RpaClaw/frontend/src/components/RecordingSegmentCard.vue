<template>
  <div class="msg-enter-left my-2 w-full">
    <div class="overflow-hidden rounded-2xl border border-violet-100 bg-white shadow-sm dark:border-violet-900/40 dark:bg-gray-950/70">
      <button
        type="button"
        data-testid="recording-segment-toggle"
        class="flex w-full items-start gap-3 px-3 py-2.5 text-left transition hover:bg-violet-50/40 dark:hover:bg-violet-950/20"
        @click="toggleExpanded"
      >
        <div class="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 text-xs font-black text-white shadow-sm shadow-violet-500/20">
          R
        </div>

        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-[10px] font-black uppercase tracking-[0.18em] text-violet-500">{{ kindLabel }}</span>
            <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500 dark:bg-gray-800 dark:text-gray-300">
              {{ statusLabel }}
            </span>
            <span v-if="tested" class="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
              {{ t('Tested') }}
            </span>
            <span
              v-if="bindingSummary.unboundCount"
              class="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
            >
              {{ t('Pending bindings count', { count: bindingSummary.unboundCount }) }}
            </span>
          </div>

          <div class="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <h3 class="max-w-full truncate text-sm font-extrabold text-gray-950 dark:text-gray-50">
              {{ title }}
            </h3>
            <span class="text-[11px] font-semibold text-gray-500 dark:text-gray-400">{{ t('segment steps count', { count: stepCount }) }}</span>
            <span class="text-[11px] font-semibold text-gray-500 dark:text-gray-400">{{ t('segment params count', { count: paramCount }) }}</span>
            <span class="text-[11px] font-semibold text-gray-500 dark:text-gray-400">{{ t('segment inputs count', { count: inputEntries.length }) }}</span>
            <span class="text-[11px] font-semibold text-gray-500 dark:text-gray-400">{{ t('segment outputs count', { count: outputEntries.length }) }}</span>
            <span
              v-if="bindingSummary.boundCount"
              class="text-[11px] font-semibold text-violet-600 dark:text-violet-300"
            >
              {{ t('segment bound count', { count: bindingSummary.boundCount }) }}
            </span>
          </div>

          <p v-if="currentSummary.description || currentSummary.intent" class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
            {{ currentSummary.description || currentSummary.intent }}
          </p>

          <div v-if="bindingSummary.lines.length" class="mt-1.5 flex flex-wrap gap-1">
            <span
              v-for="line in bindingSummary.lines"
              :key="line"
              class="rounded-md bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700 dark:bg-violet-950/40 dark:text-violet-300"
            >
              {{ line }}
            </span>
          </div>

          <div v-if="paramChips.length || inputEntries.length || outputEntries.length" class="mt-1.5 flex flex-wrap gap-1">
            <span
              v-for="item in inputEntries"
              :key="`input-${item.name}`"
              class="rounded-md bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700 dark:bg-blue-950/40 dark:text-blue-300"
            >
              {{ t('Recording Input') }} {{ item.name }}
            </span>
            <span
              v-for="item in outputEntries"
              :key="`output-${item.name}`"
              class="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
            >
              {{ t('Recording Output') }} {{ item.name }}
            </span>
            <span
              v-for="item in paramChips"
              :key="`param-${item}`"
              class="rounded-md bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
            >
              {{ t('Recording Parameter') }} {{ item }}
            </span>
          </div>
        </div>

        <div class="mt-1 flex-shrink-0 text-xs font-bold text-violet-600 dark:text-violet-300">
          {{ expanded ? t('Collapse') : t('Expand') }}
        </div>
      </button>

      <div v-if="expanded" class="border-t border-gray-100 bg-gray-50/70 px-3 py-2.5 dark:border-gray-800 dark:bg-gray-900/40">
        <div v-if="canQuickBind" class="mb-2 flex justify-end">
          <button
            type="button"
            data-testid="open-mapping-drawer"
            class="rounded-lg border border-violet-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-violet-700 transition hover:bg-violet-50 dark:border-violet-800 dark:bg-gray-950 dark:text-violet-300 dark:hover:bg-violet-950/30"
            @click.stop="openMappingDrawer"
          >
            {{ t('Edit mapping') }}
          </button>
        </div>

        <div v-if="inputEntries.length || outputEntries.length || paramDetails.length" class="mb-2 grid gap-2 md:grid-cols-2">
          <div v-if="inputEntries.length" class="rounded-xl border border-blue-100 bg-white p-2.5 dark:border-blue-900/40 dark:bg-gray-950/70">
            <div class="flex items-center justify-between gap-2">
              <div class="text-[11px] font-black uppercase tracking-wide text-blue-500">{{ t('Recording Input') }}</div>
              <span
                v-if="loadingSources"
                class="text-[11px] font-semibold text-gray-400"
              >
                {{ t('Loading sources...') }}
              </span>
            </div>
            <div class="mt-1.5 space-y-2">
              <div v-for="item in inputEntries" :key="item.name" class="rounded-xl border border-blue-50 bg-blue-50/40 p-2 dark:border-blue-900/30 dark:bg-blue-950/20">
                <div class="text-xs text-gray-700 dark:text-gray-300">
                  <span class="font-bold">{{ item.name }}</span>
                  <span class="text-gray-400"> · {{ item.type || 'string' }}</span>
                </div>
                <div class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                  {{ item.source_ref ? t('Source: {source}', { source: item.source_ref }) : t('No source bound') }}
                </div>
                <div v-if="canQuickBind" class="mt-2">
                  <select
                    :data-testid="`binding-select-${item.name}`"
                    class="w-full rounded-lg border border-blue-200 bg-white px-2.5 py-2 text-xs text-gray-700 outline-none transition focus:border-violet-400 dark:border-blue-800 dark:bg-gray-950 dark:text-gray-100"
                    :disabled="bindingInputName === item.name"
                    :value="item.source_ref || ''"
                    @click.stop
                    @change="handleBindingChange(item, $event)"
                  >
                    <option value="">{{ t('Unbound') }}</option>
                    <optgroup v-if="effectiveSourcePool.recommended.length" :label="t('Recommended sources')">
                      <option
                        v-for="option in effectiveSourcePool.recommended"
                        :key="`recommended-${option.id}`"
                        :value="option.sourceRef"
                      >
                        {{ formatSourceOption(option) }}
                      </option>
                    </optgroup>
                    <optgroup v-if="effectiveSourcePool.segmentOutputs.length" :label="t('Historical segment outputs')">
                      <option
                        v-for="option in effectiveSourcePool.segmentOutputs"
                        :key="`output-${option.id}`"
                        :value="option.sourceRef"
                      >
                        {{ formatSourceOption(option) }}
                      </option>
                    </optgroup>
                    <optgroup v-if="effectiveSourcePool.artifacts.length" :label="t('Historical artifacts')">
                      <option
                        v-for="option in effectiveSourcePool.artifacts"
                        :key="`artifact-${option.id}`"
                        :value="option.sourceRef"
                      >
                        {{ formatSourceOption(option) }}
                      </option>
                    </optgroup>
                    <optgroup v-if="effectiveSourcePool.workflowParams.length" :label="t('Workflow params')">
                      <option
                        v-for="option in effectiveSourcePool.workflowParams"
                        :key="`workflow-${option.id}`"
                        :value="option.sourceRef"
                      >
                        {{ formatSourceOption(option) }}
                      </option>
                    </optgroup>
                  </select>
                  <div v-if="bindingErrors[item.name]" class="mt-1 text-[11px] text-rose-600 dark:text-rose-300">
                    {{ bindingErrors[item.name] }}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="outputEntries.length" class="rounded-xl border border-emerald-100 bg-white p-2.5 dark:border-emerald-900/40 dark:bg-gray-950/70">
            <div class="text-[11px] font-black uppercase tracking-wide text-emerald-500">{{ t('Recording Output') }}</div>
            <div class="mt-1.5 space-y-1">
              <div v-for="item in outputEntries" :key="item.name" class="text-xs text-gray-700 dark:text-gray-300">
                <span class="font-bold">{{ item.name }}</span>
                <span class="text-gray-400"> · {{ item.type || 'string' }}</span>
              </div>
            </div>
          </div>

          <div v-if="paramDetails.length" class="rounded-xl border border-amber-100 bg-white p-2.5 dark:border-amber-900/40 dark:bg-gray-950/70">
            <div class="text-[11px] font-black uppercase tracking-wide text-amber-500">{{ t('Recording Parameter') }}</div>
            <div class="mt-1.5 space-y-1">
              <div v-for="item in paramDetails" :key="item.name" class="text-xs text-gray-700 dark:text-gray-300">
                <span class="font-bold">{{ item.name }}</span>
                <span v-if="item.sensitive" class="ml-1 text-rose-500">{{ t('Sensitive') }}</span>
                <span v-if="item.preview" class="ml-1 break-all text-gray-400">= {{ item.preview }}</span>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="steps.length"
          data-testid="recording-segment-steps"
          class="max-h-48 space-y-2 overflow-y-auto pr-1"
        >
          <div
            v-for="(step, index) in steps"
            :key="step.id"
            class="rounded-xl border border-white bg-white/90 p-2.5 dark:border-gray-800 dark:bg-gray-950/70"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="text-xs font-bold leading-snug text-gray-950 dark:text-gray-50">
                  {{ index + 1 }}. {{ step.description || step.action }}
                </div>
                <div class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
                  {{ step.target || t('No locator generated') }}
                </div>
              </div>
              <span class="rounded-full px-2 py-0.5 text-[11px] font-bold" :class="statusClass(step.validation?.status)">
                {{ step.validation?.status || 'unknown' }}
              </span>
            </div>

            <div v-if="step.locator_candidates?.length" class="mt-2">
              <div class="text-[11px] font-bold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                {{ t('Locator candidates') }}
              </div>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <button
                  v-for="(candidate, candidateIndex) in step.locator_candidates"
                  :key="`${step.id}-${candidateIndex}`"
                  class="rounded-lg border px-2 py-1 text-xs font-bold transition-colors"
                  :class="candidate.selected
                    ? 'border-emerald-500 bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                    : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800'"
                  :disabled="isRepairing(resolveStepIndex(step, index))"
                  @click.stop="switchLocator(resolveStepIndex(step, index), candidateIndex)"
                >
                  {{ candidate.kind || t('Candidate {index}', { index: candidateIndex + 1 }) }}
                </button>
              </div>
              <div v-if="repairErrors[step.id]" class="mt-2 text-xs text-rose-600 dark:text-rose-300">
                {{ repairErrors[step.id] }}
              </div>
            </div>
          </div>
        </div>

        <div v-else class="rounded-xl border border-dashed border-gray-200 bg-white/70 p-3 text-xs text-gray-500 dark:border-gray-800 dark:bg-gray-950/50 dark:text-gray-400">
          {{ t('No displayable recording steps') }}
        </div>
      </div>
    </div>
  </div>

  <div
    v-if="mappingDrawerOpen"
    class="fixed inset-0 z-[60] flex justify-end bg-black/25 backdrop-blur-[1px]"
    @click.self="mappingDrawerOpen = false"
  >
    <div class="h-full w-full max-w-3xl overflow-y-auto border-l border-violet-100 bg-white shadow-2xl dark:border-violet-900/40 dark:bg-gray-950">
      <div class="flex items-center justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800">
        <div>
          <div class="text-sm font-black text-gray-950 dark:text-gray-50">{{ t('I/O Mapping') }}</div>
          <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {{ t('bound input summary', { title, bound: bindingSummary.boundCount, total: inputEntries.length }) }}
          </div>
        </div>
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 transition hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-900"
          @click="mappingDrawerOpen = false"
        >
          {{ t('Close') }}
        </button>
      </div>

      <div class="grid gap-4 px-5 py-4 lg:grid-cols-[1.2fr_1fr]">
        <div class="space-y-3">
          <div class="text-[11px] font-black uppercase tracking-wide text-violet-500">{{ t('Current inputs') }}</div>
          <div
            v-for="item in inputEntries"
            :key="`drawer-${item.name}`"
            class="rounded-2xl border border-violet-100 bg-violet-50/30 p-3 dark:border-violet-900/30 dark:bg-violet-950/10"
          >
            <div class="flex items-center justify-between gap-2">
              <div>
                <div class="text-sm font-bold text-gray-900 dark:text-gray-50">{{ item.name }}</div>
                <div class="text-xs text-gray-500 dark:text-gray-400">{{ item.type || 'string' }}</div>
              </div>
              <span
                class="rounded-full px-2 py-0.5 text-[10px] font-bold"
                :class="item.source_ref
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300'"
              >
                {{ item.source_ref ? t('Bound') : t('Pending binding') }}
              </span>
            </div>
            <div class="mt-2 text-xs text-gray-500 dark:text-gray-400">
              {{ item.source_ref || t('No source selected') }}
            </div>
            <div class="mt-3">
              <select
                class="w-full rounded-xl border border-violet-200 bg-white px-3 py-2 text-xs text-gray-700 outline-none transition focus:border-violet-400 dark:border-violet-800 dark:bg-gray-950 dark:text-gray-100"
                :value="item.source_ref || ''"
                :disabled="bindingInputName === item.name"
                @change="handleBindingChange(item, $event)"
              >
                <option value="">{{ t('Unbound') }}</option>
                <optgroup v-if="effectiveSourcePool.recommended.length" :label="t('Recommended sources')">
                  <option
                    v-for="option in effectiveSourcePool.recommended"
                    :key="`drawer-recommended-${option.id}`"
                    :value="option.sourceRef"
                  >
                    {{ formatSourceOption(option) }}
                  </option>
                </optgroup>
                <optgroup v-if="effectiveSourcePool.segmentOutputs.length" :label="t('Historical segment outputs')">
                  <option
                    v-for="option in effectiveSourcePool.segmentOutputs"
                    :key="`drawer-output-${option.id}`"
                    :value="option.sourceRef"
                  >
                    {{ formatSourceOption(option) }}
                  </option>
                </optgroup>
                <optgroup v-if="effectiveSourcePool.artifacts.length" :label="t('Historical artifacts')">
                  <option
                    v-for="option in effectiveSourcePool.artifacts"
                    :key="`drawer-artifact-${option.id}`"
                    :value="option.sourceRef"
                  >
                    {{ formatSourceOption(option) }}
                  </option>
                </optgroup>
              </select>
              <div v-if="bindingErrors[item.name]" class="mt-2 text-[11px] text-rose-600 dark:text-rose-300">
                {{ bindingErrors[item.name] }}
              </div>
            </div>
          </div>
        </div>

        <div class="space-y-3">
          <div class="text-[11px] font-black uppercase tracking-wide text-violet-500">{{ t('Available sources') }}</div>
          <div class="rounded-2xl border border-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-950/60">
            <div class="text-xs font-bold text-gray-700 dark:text-gray-300">{{ t('Recommended sources') }}</div>
            <div v-if="effectiveSourcePool.recommended.length" class="mt-2 space-y-2">
              <div
                v-for="option in effectiveSourcePool.recommended"
                :key="`recommended-preview-${option.id}`"
                class="rounded-xl border border-violet-100 bg-violet-50/30 p-2 text-xs dark:border-violet-900/30 dark:bg-violet-950/10"
              >
                <div class="font-semibold text-gray-900 dark:text-gray-50">{{ option.segmentTitle || t('Workflow param') }}</div>
                <div class="mt-1 text-gray-500 dark:text-gray-400">{{ option.name }} · {{ option.valueType }}</div>
                <div v-if="option.preview" class="mt-1 break-all text-gray-400">{{ option.preview }}</div>
              </div>
            </div>
            <div v-else class="mt-2 text-xs text-gray-400">{{ t('No recommended sources') }}</div>
          </div>

          <div class="rounded-2xl border border-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-950/60">
            <div class="text-xs font-bold text-gray-700 dark:text-gray-300">{{ t('Historical segment outputs') }}</div>
            <div v-if="effectiveSourcePool.segmentOutputs.length" class="mt-2 space-y-2">
              <div
                v-for="option in effectiveSourcePool.segmentOutputs"
                :key="`output-preview-${option.id}`"
                class="rounded-xl border border-emerald-100 bg-emerald-50/30 p-2 text-xs dark:border-emerald-900/30 dark:bg-emerald-950/10"
              >
                <div class="font-semibold text-gray-900 dark:text-gray-50">{{ option.segmentTitle }}</div>
                <div class="mt-1 text-gray-500 dark:text-gray-400">{{ option.name }} · {{ option.valueType }}</div>
              </div>
            </div>
            <div v-else class="mt-2 text-xs text-gray-400">{{ t('No historical outputs') }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getRecordingSegmentMappingSources,
  promoteRecordingSegmentStepLocator,
  promoteRecordingStepLocator,
  updateRecordingSegmentBindings,
} from '@/api/recording'
import type {
  MappingSourceOption,
  MappingSourcePool,
  RecordingSegmentSummary,
  RecordingStep,
  RecordingSegmentUpdatedPayload,
  WorkflowIO,
} from '@/types/recording'
import {
  buildMappingSourcePool,
  deriveSummaryInputs,
  deriveSummaryOutputs,
  summarizeInputBindings,
} from '@/utils/recording'

const props = withDefaults(defineProps<{
  summary: RecordingSegmentSummary
  sessionId?: string
  runId?: string
  summaries?: RecordingSegmentSummary[]
}>(), {
  sessionId: '',
  runId: '',
  summaries: () => [],
})

const emit = defineEmits<{
  segmentUpdated: [payload: RecordingSegmentUpdatedPayload]
}>()

const { t } = useI18n()
const expanded = ref(false)
const currentSummary = ref<RecordingSegmentSummary>({ ...props.summary })
const steps = ref<RecordingStep[]>(props.summary.steps ? [...props.summary.steps] : [])
const repairingStepIndex = ref<number | null>(null)
const repairErrors = ref<Record<string, string>>({})
const emptySourcePool: MappingSourcePool = {
  recommended: [],
  segmentOutputs: [],
  artifacts: [],
  workflowParams: [],
}
const loadingSources = ref(false)
const sourcePool = ref<MappingSourcePool>({ ...emptySourcePool })
const sourcePoolLoaded = ref(false)
const bindingInputName = ref<string | null>(null)
const bindingErrors = ref<Record<string, string>>({})
const mappingDrawerOpen = ref(false)

const title = computed(() => currentSummary.value.title || currentSummary.value.intent || t('Completed recording segment'))
const stepCount = computed(() => steps.value.length)
const paramCount = computed(() => Object.keys(currentSummary.value.params || {}).length)
const kindLabel = computed(() => {
  if (currentSummary.value.kind === 'script') return t('Script Segment')
  if (currentSummary.value.kind === 'mcp') return t('MCP Segment')
  if (currentSummary.value.kind === 'llm') return t('LLM Segment')
  return t('Workflow Segment')
})
const statusLabel = computed(() => t(currentSummary.value.status || 'completed'))
const inputEntries = computed<WorkflowIO[]>(() => deriveSummaryInputs(currentSummary.value))
const outputEntries = computed<WorkflowIO[]>(() => deriveSummaryOutputs(currentSummary.value))
const rpaSessionId = computed(() => currentSummary.value.rpa_session_id || currentSummary.value.session_id || '')
const paramChips = computed(() => Object.keys(currentSummary.value.params || {}).slice(0, 4))
const paramDetails = computed(() => {
  const params = currentSummary.value.params || {}
  return Object.entries(params).map(([name, config]) => {
    const raw = config?.original_value
    const preview = raw === undefined || raw === null
      ? ''
      : String(raw).length > 48 ? `${String(raw).slice(0, 48)}...` : String(raw)
    return {
      name,
      sensitive: !!config?.sensitive,
      preview,
    }
  })
})
const tested = computed(() => currentSummary.value.testing_status === 'passed')
const fallbackSourcePool = computed(() =>
  buildMappingSourcePool({
    currentSegmentId: currentSummary.value.segment_id,
    summaries: props.summaries,
    workflowParams: [],
  }),
)
const effectiveSourcePool = computed<MappingSourcePool>(() =>
  sourcePoolLoaded.value
    ? (sourcePool.value || emptySourcePool)
    : (fallbackSourcePool.value || emptySourcePool),
)
const bindingSummary = computed(() => summarizeInputBindings(inputEntries.value))
const canQuickBind = computed(() => !!props.sessionId && !!props.runId && inputEntries.value.length > 0)

watch(
  () => props.summary,
  (nextSummary) => {
    currentSummary.value = { ...nextSummary }
    steps.value = nextSummary.steps ? [...nextSummary.steps] : []
    repairErrors.value = {}
    bindingErrors.value = {}
  },
  { immediate: true, deep: true },
)

watch(
  () => currentSummary.value.segment_id,
  () => {
    sourcePoolLoaded.value = false
    sourcePool.value = { ...emptySourcePool }
  },
)

const statusClass = (status?: string) => {
  if (status === 'ok') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
  if (status === 'fallback' || status === 'ambiguous') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
  if (status === 'broken') return 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300'
  return 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
}

const isRepairing = (stepIndex?: number) => stepIndex !== undefined && repairingStepIndex.value === stepIndex

const resolveStepIndex = (step: RecordingStep, fallbackIndex: number) =>
  typeof step.step_index === 'number' ? step.step_index : fallbackIndex

const toggleExpanded = async () => {
  expanded.value = !expanded.value
  if (expanded.value) {
    await ensureSourcePool()
  }
}

const openMappingDrawer = async () => {
  mappingDrawerOpen.value = true
  await ensureSourcePool()
}

const ensureSourcePool = async () => {
  if (!canQuickBind.value || sourcePoolLoaded.value || loadingSources.value) {
    return
  }

  loadingSources.value = true
  try {
    const data = await getRecordingSegmentMappingSources(
      props.sessionId,
      props.runId,
      currentSummary.value.segment_id,
    )
    sourcePool.value = data.sourcePool
    sourcePoolLoaded.value = true
    if (data.summary) {
      currentSummary.value = { ...currentSummary.value, ...data.summary }
      steps.value = data.summary.steps ? [...data.summary.steps] : []
    }
  } catch {
    sourcePool.value = fallbackSourcePool.value
    sourcePoolLoaded.value = true
  } finally {
    loadingSources.value = false
  }
}

const findSourceOption = (sourceRef: string) => {
  const pool = effectiveSourcePool.value
  return [
    ...pool.recommended,
    ...pool.segmentOutputs,
    ...pool.artifacts,
    ...pool.workflowParams,
  ].find((item) => item.sourceRef === sourceRef)
}

const formatSourceOption = (option: MappingSourceOption) =>
  [option.segmentTitle || t('Workflow param'), option.name, option.valueType].filter(Boolean).join(' · ')

const handleBindingChange = async (item: WorkflowIO, event: Event) => {
  const value = (event.target as HTMLSelectElement | null)?.value || ''
  bindingErrors.value = { ...bindingErrors.value, [item.name]: '' }
  bindingInputName.value = item.name

  const nextInputs: WorkflowIO[] = inputEntries.value.map((entry): WorkflowIO => {
    if (entry.name !== item.name) {
      return { ...entry }
    }
    if (!value) {
      return {
        ...entry,
        source: entry.source === 'credential' ? 'credential' : 'user',
        source_ref: null,
      }
    }
    const option = findSourceOption(value)
    return {
      ...entry,
      type: option?.valueType || entry.type,
      source: option?.sourceType || entry.source,
      source_ref: value,
    }
  })

  try {
    const data = await updateRecordingSegmentBindings(
      props.sessionId,
      props.runId,
      currentSummary.value.segment_id,
      nextInputs,
    )
    currentSummary.value = { ...currentSummary.value, ...data.summary }
    steps.value = data.summary.steps ? [...data.summary.steps] : []
    sourcePool.value = data.sourcePool
    sourcePoolLoaded.value = true
    emit('segmentUpdated', {
      run: data.run,
      summaries: [data.summary],
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : t('Binding update failed')
    bindingErrors.value = { ...bindingErrors.value, [item.name]: message }
  } finally {
    bindingInputName.value = null
  }
}

const switchLocator = async (stepIndex: number | undefined, candidateIndex: number) => {
  const hasSegmentContext = !!props.sessionId && !!props.runId && !!currentSummary.value.segment_id
  if (stepIndex === undefined || (!rpaSessionId.value && !hasSegmentContext)) return

  const currentStep = steps.value.find((item, index) => resolveStepIndex(item, index) === stepIndex)
  if (!currentStep) return

  repairingStepIndex.value = stepIndex
  repairErrors.value = { ...repairErrors.value, [currentStep.id]: '' }

  try {
    if (hasSegmentContext) {
      const data = await promoteRecordingSegmentStepLocator(
        props.sessionId,
        props.runId,
        currentSummary.value.segment_id,
        stepIndex,
        candidateIndex,
        rpaSessionId.value,
      )
      currentSummary.value = { ...currentSummary.value, ...data.summary }
      steps.value = data.summary.steps ? [...data.summary.steps] : steps.value
      emit('segmentUpdated', {
        run: data.run,
        summaries: [data.summary],
      })
    } else {
      const nextStep = await promoteRecordingStepLocator(rpaSessionId.value, stepIndex, candidateIndex)
      steps.value = steps.value.map((item, index) =>
        resolveStepIndex(item, index) === stepIndex
          ? {
              ...item,
              ...nextStep,
              step_index: stepIndex,
            }
          : item,
      )
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : t('Locator switch failed')
    repairErrors.value = { ...repairErrors.value, [currentStep.id]: message }
  } finally {
    repairingStepIndex.value = null
  }
}
</script>

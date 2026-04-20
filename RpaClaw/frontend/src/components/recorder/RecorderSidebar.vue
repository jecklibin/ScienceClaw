<script setup lang="ts">
interface RecorderStep {
  id?: string
  title?: string
  description?: string
  action?: string
  status?: string
  source?: string
  locatorSummary?: string
  frameSummary?: string
  validationStatus?: string
  validationDetails?: string
}

defineProps<{
  steps: RecorderStep[]
}>()
</script>

<template>
  <aside class="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-r border-slate-200 bg-slate-100 p-5 dark:border-slate-800 dark:bg-slate-950">
    <div class="mb-5 flex items-center justify-between">
      <h2 class="text-base font-extrabold text-slate-950 dark:text-slate-100">录制步骤</h2>
      <span class="rounded-full bg-sky-100 px-2.5 py-1 text-[11px] font-bold text-sky-700 dark:bg-sky-950 dark:text-sky-300">
        {{ steps.length }} 步
      </span>
    </div>

    <div v-if="steps.length" class="space-y-3">
      <article
        v-for="(step, index) in steps"
        :key="step.id || index"
        class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div class="mb-1 flex items-center justify-between gap-3">
          <span class="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">Step {{ index + 1 }}</span>
          <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {{ step.status || 'pending' }}
          </span>
        </div>
        <h3 class="text-sm font-bold text-slate-950 dark:text-slate-100">
          {{ step.title || step.description || step.action || '未命名步骤' }}
        </h3>
        <p v-if="step.description" class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          {{ step.description }}
        </p>
        <p v-if="step.locatorSummary" class="mt-2 truncate rounded-lg bg-slate-50 px-2 py-1 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {{ step.locatorSummary }}
        </p>
        <p v-if="step.validationStatus" class="mt-2 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
          {{ step.validationStatus }} {{ step.validationDetails ? `· ${step.validationDetails}` : '' }}
        </p>
      </article>
    </div>

    <div v-else class="rounded-2xl border border-dashed border-slate-300 p-6 text-center text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
      暂无步骤，开始操作后会自动记录。
    </div>
  </aside>
</template>

<template>
  <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
    <div class="mb-4">
      <h3 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ title }}</h3>
      <p v-if="description" class="mt-1 text-sm text-slate-500 dark:text-slate-400">{{ description }}</p>
    </div>

    <div v-if="steps.length === 0" class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
      {{ t('No steps available') }}
    </div>

    <div v-else class="space-y-3">
      <article
        v-for="(step, index) in steps"
        :key="String(step.id || index)"
        class="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50/80 dark:border-white/10 dark:bg-white/[0.03]"
      >
        <button
          type="button"
          class="flex w-full items-start gap-4 px-4 py-4 text-left"
          @click="expandedIndex = expandedIndex === index ? null : index"
        >
          <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-sm font-black text-white dark:bg-white dark:text-slate-900">
            {{ String(index + 1).padStart(2, '0') }}
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-600 ring-1 ring-slate-200 dark:bg-white/[0.06] dark:text-slate-300 dark:ring-white/10">
                {{ step.action || t('Step') }}
              </span>
              <span
                v-if="step.validation?.status"
                class="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200"
              >
                {{ step.validation.status }}
              </span>
            </div>
            <h4 class="mt-2 text-sm font-bold text-slate-900 dark:text-slate-100">{{ getStepTitle(step) }}</h4>
            <p v-if="getStepHint(step)" class="mt-2 text-xs text-slate-500 dark:text-slate-400">{{ getStepHint(step) }}</p>
          </div>
        </button>
        <div v-if="expandedIndex === index" class="border-t border-slate-200 px-4 py-4 dark:border-white/10">
          <pre class="overflow-x-auto rounded-2xl bg-white p-3 text-xs text-slate-600 ring-1 ring-slate-200 dark:bg-[#15171d] dark:text-slate-300 dark:ring-white/10"><code>{{ JSON.stringify(step, null, 2) }}</code></pre>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

withDefaults(defineProps<{
  steps: Array<Record<string, any>>;
  title?: string;
  description?: string;
}>(), {
  title: '',
  description: '',
});

const { t } = useI18n();
const expandedIndex = ref<number | null>(0);

function getStepTitle(step: Record<string, any>) {
  return step.description || step.label || step.url || step.target || step.action || t('Step');
}

function getStepHint(step: Record<string, any>) {
  if (Array.isArray(step.frame_path) && step.frame_path.length) {
    return step.frame_path.join(' -> ');
  }
  if (step.validation?.details) {
    return String(step.validation.details);
  }
  return step.value ? String(step.value) : '';
}
</script>

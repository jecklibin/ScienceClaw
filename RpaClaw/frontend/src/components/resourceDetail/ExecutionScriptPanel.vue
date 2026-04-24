<template>
  <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
    <div class="mb-4">
      <h3 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ title }}</h3>
      <p v-if="description" class="mt-1 text-sm text-slate-500 dark:text-slate-400">{{ description }}</p>
    </div>

    <div v-if="loading" class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
      {{ t('Loading execution script...') }}
    </div>
    <div v-else-if="error" class="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-6 text-sm text-rose-700 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-200">
      {{ error }}
    </div>
    <div v-else-if="script" class="space-y-3">
      <div v-if="generatedAt" class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {{ t('Generated at') }} · {{ generatedAt }}
      </div>
      <pre class="overflow-x-auto rounded-2xl bg-[#0f172a] p-4 text-xs text-slate-100"><code>{{ script }}</code></pre>
    </div>
    <div v-else class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
      {{ t('No execution script available') }}
    </div>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';

withDefaults(defineProps<{
  script?: string;
  loading?: boolean;
  error?: string | null;
  title?: string;
  description?: string;
  generatedAt?: string;
}>(), {
  script: '',
  loading: false,
  error: null,
  title: '',
  description: '',
  generatedAt: '',
});

const { t } = useI18n();
</script>

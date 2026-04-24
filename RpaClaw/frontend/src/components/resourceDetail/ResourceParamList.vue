<template>
  <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
    <div class="mb-4">
      <h3 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ title }}</h3>
      <p v-if="description" class="mt-1 text-sm text-slate-500 dark:text-slate-400">{{ description }}</p>
    </div>

    <div v-if="rows.length === 0" class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
      {{ t('No parameters configured') }}
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="row in rows"
        :key="row.name"
        class="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 dark:border-white/10 dark:bg-white/[0.03]"
      >
        <div class="flex flex-wrap items-center gap-2">
          <h4 class="text-sm font-bold text-slate-900 dark:text-slate-100">{{ row.name }}</h4>
          <span class="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-500 ring-1 ring-slate-200 dark:bg-white/[0.06] dark:text-slate-300 dark:ring-white/10">
            {{ row.type }}
          </span>
          <span
            v-if="row.required"
            class="rounded-full bg-rose-100 px-2.5 py-1 text-[11px] font-semibold text-rose-700 dark:bg-rose-500/15 dark:text-rose-200"
          >
            {{ t('Required') }}
          </span>
          <span
            v-if="row.sensitive"
            class="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-semibold text-amber-700 dark:bg-amber-500/15 dark:text-amber-200"
          >
            {{ t('Sensitive') }}
          </span>
        </div>
        <p v-if="row.description" class="mt-2 text-sm text-slate-600 dark:text-slate-300">{{ row.description }}</p>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('Default') }}</div>
            <pre class="mt-1 whitespace-pre-wrap break-all rounded-xl bg-white px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200 dark:bg-[#15171d] dark:text-slate-300 dark:ring-white/10">{{ row.defaultValue }}</pre>
          </div>
          <div v-if="row.source">
            <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('Source') }}</div>
            <div class="mt-1 rounded-xl bg-white px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200 dark:bg-[#15171d] dark:text-slate-300 dark:ring-white/10">{{ row.source }}</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

type ParamInput = Record<string, unknown> | Array<Record<string, unknown>>;

const props = withDefaults(defineProps<{
  params: ParamInput;
  title?: string;
  description?: string;
}>(), {
  title: '',
  description: '',
});

const { t } = useI18n();

const rows = computed(() => {
  const source = props.params;
  if (Array.isArray(source)) {
    return source.map((item, index) => ({
      name: String(item.name || item.key || item.sourceKey || `param_${index + 1}`),
      type: String(item.type || 'string'),
      description: String(item.description || ''),
      required: Boolean(item.required),
      sensitive: Boolean(item.sensitive),
      defaultValue: formatValue(item.defaultValue ?? item.default ?? item.originalValue ?? item.original_value),
      source: item.source ? String(item.source) : (item.sourceKey ? String(item.sourceKey) : ''),
    }));
  }

  return Object.entries(source || {}).map(([name, value]) => {
    const item = value && typeof value === 'object' ? value as Record<string, unknown> : {};
    return {
      name,
      type: String(item.type || 'string'),
      description: String(item.description || ''),
      required: Boolean(item.required),
      sensitive: Boolean(item.sensitive),
      defaultValue: formatValue(item.defaultValue ?? item.default ?? item.originalValue ?? item.original_value),
      source: item.source_param ? String(item.source_param) : '',
    };
  });
});

function formatValue(value: unknown) {
  if (value === undefined || value === null || value === '') {
    return '—';
  }
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value, null, 2);
}
</script>

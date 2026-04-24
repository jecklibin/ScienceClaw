<template>
  <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
    <div class="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0 space-y-3">
        <div class="flex flex-wrap items-center gap-2">
          <span class="rounded-full bg-slate-900 px-3 py-1 text-xs font-bold uppercase tracking-wide text-white dark:bg-white dark:text-slate-900">
            {{ kindLabel }}
          </span>
          <span
            v-for="badge in badges"
            :key="badge"
            class="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600 dark:bg-white/[0.06] dark:text-slate-300"
          >
            {{ badge }}
          </span>
        </div>
        <div>
          <h2 class="text-2xl font-black text-slate-900 dark:text-slate-100">{{ title }}</h2>
          <p v-if="description" class="mt-2 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">
            {{ description }}
          </p>
        </div>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div
          v-for="count in counts"
          :key="count.label"
          class="min-w-[88px] rounded-2xl bg-slate-50 px-4 py-3 text-center dark:bg-white/[0.03]"
        >
          <div class="text-2xl font-black text-slate-900 dark:text-slate-100">{{ count.value }}</div>
          <div class="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ count.label }}</div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string;
  description?: string;
  kindLabel: string;
  badges?: string[];
  counts?: Array<{ label: string; value: number | string }>;
}>(), {
  description: '',
  badges: () => [],
  counts: () => [],
});
</script>

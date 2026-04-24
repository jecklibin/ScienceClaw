<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <slot name="header" />
    <div class="border-b border-slate-200 bg-white/85 px-6 py-3 backdrop-blur-sm dark:border-white/10 dark:bg-white/[0.03]">
      <div class="inline-flex rounded-full bg-slate-100 p-1 dark:bg-white/[0.06]">
        <button
          data-testid="detail-tab-overview"
          class="rounded-full px-4 py-2 text-sm font-semibold transition-colors"
          :class="tab === 'overview' ? 'bg-white text-slate-900 shadow-sm dark:bg-[#1a1c22] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'"
          @click="$emit('update:tab', 'overview')"
        >
          {{ t('Overview') }}
        </button>
        <button
          data-testid="detail-tab-files"
          class="rounded-full px-4 py-2 text-sm font-semibold transition-colors"
          :class="tab === 'files' ? 'bg-white text-slate-900 shadow-sm dark:bg-[#1a1c22] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'"
          @click="$emit('update:tab', 'files')"
        >
          {{ t('Files') }}
        </button>
      </div>
    </div>
    <div class="min-h-0 flex-1 overflow-hidden">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';

defineProps<{
  tab: 'overview' | 'files';
}>();

defineEmits<{
  (event: 'update:tab', value: 'overview' | 'files'): void;
}>();

const { t } = useI18n();
</script>

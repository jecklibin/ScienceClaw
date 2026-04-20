<script setup lang="ts">
defineProps<{
  testingState?: {
    status?: 'idle' | 'running' | 'failed' | 'passed' | string
    failed_step_index?: number | null
    error?: string
  }
}>()
</script>

<template>
  <section class="w-72 shrink-0 border-l border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
    <h2 class="text-sm font-extrabold text-slate-950 dark:text-slate-100">测试验证</h2>
    <p class="mt-2 rounded-2xl bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      状态：{{ testingState?.status || 'idle' }}
    </p>
    <p v-if="testingState?.failed_step_index !== undefined && testingState?.failed_step_index !== null" class="mt-3 text-xs text-red-600 dark:text-red-400">
      失败步骤：{{ testingState.failed_step_index + 1 }}
    </p>
    <p v-if="testingState?.error" class="mt-3 text-xs text-red-600 dark:text-red-400">
      {{ testingState.error }}
    </p>
  </section>
</template>

<template>
  <div v-if="artifacts.length" class="mb-3 rounded-2xl border border-gray-200/70 bg-white/90 p-3 shadow-sm dark:border-gray-700/60 dark:bg-gray-900/70">
    <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
      当前产物
    </div>
    <div class="flex flex-col gap-2">
      <div
        v-for="(artifact, index) in artifacts"
        :key="artifact.id || `${artifact.name}-${index}`"
        class="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-800/80"
      >
        <div class="font-medium text-gray-800 dark:text-gray-100">{{ artifact.name }}</div>
        <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ artifact.type }}</div>
        <div v-if="artifact.path" class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">{{ artifact.path }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { RecordingArtifact } from '@/types/recording'

defineProps<{
  artifacts: RecordingArtifact[]
}>()
</script>

<template>
  <div
    v-if="visible && route"
    class="fixed inset-0 z-[80] flex items-center justify-center bg-gray-950/35 p-4 backdrop-blur-md sm:p-6"
  >
    <div class="flex h-[min(900px,94vh)] w-full max-w-[1440px] flex-col overflow-hidden rounded-3xl bg-white shadow-[0_28px_90px_-32px_rgba(15,23,42,0.7)] ring-1 ring-black/5 dark:bg-gray-950">
      <div class="flex flex-shrink-0 items-center justify-between border-b border-gray-100 bg-white px-5 py-4 dark:border-gray-800 dark:bg-gray-950">
        <div class="flex min-w-0 items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-rose-50 dark:bg-rose-950/50">
            <span class="h-3 w-3 rounded-full bg-rose-500 shadow-[0_0_0_8px_rgba(244,63,94,0.12)] animate-pulse" />
          </div>
          <div class="min-w-0">
            <h2 class="truncate text-base font-extrabold text-gray-950 dark:text-gray-50">业务流程录制中</h2>
            <p class="truncate text-xs text-gray-500 dark:text-gray-400">在弹窗内完成浏览器操作，完成后会回到当前对话继续测试与发布。</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="hidden rounded-full bg-gray-100 px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-gray-500 dark:bg-gray-900 dark:text-gray-400 sm:inline-flex">
            Overlay
          </span>
          <button
            class="rounded-full border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 transition hover:bg-gray-50 dark:border-gray-800 dark:text-gray-300 dark:hover:bg-gray-900"
            type="button"
            @click="$emit('close')"
          >
            返回对话
          </button>
        </div>
      </div>

      <div class="min-h-0 flex-1 bg-[#f5f6f7] p-3 dark:bg-gray-900">
        <iframe
          :src="iframeSrc"
          class="h-full w-full rounded-2xl border border-gray-200 bg-white dark:border-gray-800"
          title="RPA Recorder"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'

import type { RecordingSegmentCapturedPayload, RecordingSegmentCompletedPayload } from '@/types/recording'

const props = defineProps<{
  visible: boolean
  route: { path: string; query: Record<string, string> } | null
}>()

const emit = defineEmits<{
  close: []
  recordingCaptured: [payload: RecordingSegmentCapturedPayload]
  segmentComplete: [payload: RecordingSegmentCompletedPayload]
}>()

const iframeSrc = computed(() => {
  if (!props.route) return ''
  const params = new URLSearchParams(props.route.query)
  return `${props.route.path}?${params.toString()}`
})

const onMessage = (event: MessageEvent) => {
  if (event.origin !== window.location.origin) return
  if (event.data?.type === 'rpa-recording-captured' && event.data.payload) {
    emit('recordingCaptured', event.data.payload as RecordingSegmentCapturedPayload)
  } else if (event.data?.type === 'rpa-recording-completed' && event.data.payload) {
    emit('segmentComplete', event.data.payload as RecordingSegmentCompletedPayload)
  } else if (event.data?.type === 'rpa-recording-close') {
    emit('close')
  }
}

onMounted(() => {
  window.addEventListener('message', onMessage)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
})
</script>

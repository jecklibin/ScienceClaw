<script setup lang="ts">
import { onMounted, ref } from 'vue'
interface BrowserTab {
  tab_id: string
  title: string
  url?: string
  active?: boolean
}

defineProps<{
  address?: string
  tabs?: BrowserTab[]
  showCanvas?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:address', value: string): void
  (e: 'submit-address'): void
  (e: 'activate-tab', tabId: string): void
  (e: 'canvas-event', event: Event): void
  (e: 'canvas-ready', canvas: HTMLCanvasElement): void
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)

onMounted(() => {
  if (canvasRef.value) {
    emit('canvas-ready', canvasRef.value)
  }
})
</script>

<template>
  <section class="flex min-w-0 flex-1 flex-col overflow-hidden bg-slate-950">
    <div class="flex items-center gap-2 border-b border-slate-800 bg-white px-3 py-2 dark:bg-slate-900">
      <input
        :value="address || 'about:blank'"
        class="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        placeholder="输入网址后回车"
        @input="emit('update:address', ($event.target as HTMLInputElement).value)"
        @keydown.enter.prevent="emit('submit-address')"
      >
      <div class="flex max-w-[38%] items-center gap-1 overflow-x-auto">
        <button
          v-for="tab in tabs || []"
          :key="tab.tab_id"
          class="whitespace-nowrap rounded-lg px-2.5 py-1 text-xs font-semibold"
          :class="tab.active ? 'bg-sky-600 text-white' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'"
          @click="emit('activate-tab', tab.tab_id)"
        >
          {{ tab.title || '新标签页' }}
        </button>
      </div>
    </div>

    <div class="relative min-h-0 flex-1 bg-black">
      <canvas
        v-if="showCanvas !== false"
        ref="canvasRef"
        class="h-full w-full bg-black object-contain"
        tabindex="0"
        @mousedown.prevent="emit('canvas-event', $event)"
        @mouseup.prevent="emit('canvas-event', $event)"
        @mousemove.prevent="emit('canvas-event', $event)"
        @wheel.prevent="emit('canvas-event', $event)"
        @keydown.prevent="emit('canvas-event', $event)"
        @keyup.prevent="emit('canvas-event', $event)"
      />
      <div v-else class="flex h-full min-h-[280px] items-center justify-center text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
        Recorder Canvas Stage
      </div>
      <slot />
    </div>
  </section>
</template>

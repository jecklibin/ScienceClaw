<script setup lang="ts">
import RecorderAssistantPanel from './RecorderAssistantPanel.vue'
import RecorderCanvasStage from './RecorderCanvasStage.vue'
import RecorderSidebar from './RecorderSidebar.vue'
import RecorderTestPanel from './RecorderTestPanel.vue'

interface BrowserTab {
  tab_id: string
  title: string
  url?: string
  active?: boolean
}

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

interface AssistantMessage {
  role: 'user' | 'assistant'
  text: string
  time?: string
  status?: string
}

defineProps<{
  title: string
  subtitle?: string
  steps: RecorderStep[]
  messages: AssistantMessage[]
  testingState?: Record<string, any>
  pendingConfirm?: { description?: string; risk_reason?: string } | null
  agentRunning?: boolean
  address?: string
  tabs?: BrowserTab[]
  showCanvas?: boolean
  compact?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'complete'): void
  (e: 'update:address', value: string): void
  (e: 'submit-address'): void
  (e: 'activate-tab', tabId: string): void
  (e: 'canvas-event', event: Event): void
  (e: 'canvas-ready', canvas: HTMLCanvasElement): void
}>()
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900">
    <header class="flex shrink-0 items-center gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
      <div class="min-w-0 flex-1">
        <h1 class="truncate text-sm font-extrabold text-slate-950 dark:text-slate-100">{{ title }}</h1>
        <p class="truncate text-xs text-slate-500 dark:text-slate-400">{{ subtitle || '录制、测试、修复和发布工作台' }}</p>
      </div>
      <button
        class="rounded-xl bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500"
        @click="emit('complete')"
      >
        结束本段
      </button>
      <button
        class="rounded-xl border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        @click="emit('close')"
      >
        收起
      </button>
    </header>

    <div class="flex min-h-0 flex-1">
      <RecorderSidebar :steps="steps" />
      <div class="flex min-w-0 flex-1 flex-col">
        <RecorderCanvasStage
          :address="address"
          :tabs="tabs"
          :show-canvas="showCanvas"
          @update:address="emit('update:address', $event)"
          @submit-address="emit('submit-address')"
          @activate-tab="emit('activate-tab', $event)"
          @canvas-event="emit('canvas-event', $event)"
          @canvas-ready="emit('canvas-ready', $event)"
        >
          <slot name="canvas-overlay" />
        </RecorderCanvasStage>
        <RecorderAssistantPanel
          v-if="!compact"
          :messages="messages"
          :pending-confirm="pendingConfirm"
          :agent-running="agentRunning"
        />
      </div>
      <RecorderTestPanel v-if="!compact" :testing-state="testingState" />
    </div>
  </div>
</template>

<script setup lang="ts">
interface AssistantMessage {
  role: 'user' | 'assistant'
  text: string
  time?: string
  status?: string
}

defineProps<{
  messages: AssistantMessage[]
  pendingConfirm?: { description?: string; risk_reason?: string } | null
  agentRunning?: boolean
}>()
</script>

<template>
  <section class="flex h-72 shrink-0 flex-col border-t border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950">
    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
      <div>
        <h2 class="text-sm font-extrabold text-slate-950 dark:text-slate-100">AI 录制助手</h2>
        <p class="text-[11px] font-semibold" :class="agentRunning ? 'text-orange-500' : 'text-sky-600 dark:text-sky-400'">
          {{ agentRunning ? 'Agent 运行中...' : '已就绪 · 协助录制中' }}
        </p>
      </div>
    </div>

    <div class="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <p v-if="!messages.length" class="rounded-2xl border border-dashed border-slate-300 p-4 text-center text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
        在浏览器中操作或向助手描述目标，步骤会同步记录。
      </p>
      <article
        v-for="(message, index) in messages"
        :key="index"
        class="max-w-[88%] rounded-2xl px-3 py-2 text-xs leading-relaxed shadow-sm"
        :class="message.role === 'user' ? 'ml-auto bg-sky-600 text-white' : 'bg-white text-slate-700 dark:bg-slate-900 dark:text-slate-200'"
      >
        {{ message.text }}
      </article>

      <div v-if="pendingConfirm" class="rounded-2xl border border-orange-200 bg-orange-50 p-3 text-xs text-orange-700 dark:border-orange-900 dark:bg-orange-950/50 dark:text-orange-300">
        <p class="font-bold">高危操作确认</p>
        <p class="mt-1">{{ pendingConfirm.description }}</p>
        <p v-if="pendingConfirm.risk_reason" class="mt-1 text-[11px]">{{ pendingConfirm.risk_reason }}</p>
      </div>
    </div>
  </section>
</template>

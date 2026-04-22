<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CheckCircle, Loader2, Play, XCircle } from 'lucide-vue-next'

import { executeWorkflowRecordingTest } from '@/api/recording'

type WorkflowSegmentResult = {
  id: string
  kind: string
  title: string
  purpose: string
  status: string
}

type WorkflowExecutionResponse = {
  segments?: WorkflowSegmentResult[]
  result?: {
    success?: boolean
    logs?: string[]
    result?: {
      outputs?: Record<string, unknown>
    }
  }
}

const route = useRoute()
const router = useRouter()

const chatSessionId = computed(() => route.query.chatSessionId as string | undefined)
const runId = computed(() => route.query.runId as string | undefined)
const returnTo = computed(() => (route.query.returnTo as string | undefined) || (chatSessionId.value ? `/chat/${chatSessionId.value}` : '/chat'))

const loading = ref(true)
const success = ref(false)
const error = ref<string | null>(null)
const logs = ref<string[]>([])
const outputs = ref<Record<string, unknown>>({})
const segments = ref<WorkflowSegmentResult[]>([])

const applyExecutionResult = (response: WorkflowExecutionResponse) => {
  success.value = !!response.result?.success
  logs.value = response.result?.logs || []
  outputs.value = response.result?.result?.outputs || {}
  segments.value = response.segments || []
  if (!success.value && !logs.value.length) {
    logs.value = ['整体测试执行失败，但没有返回可用日志。']
  }
}

const runWorkflowTest = async () => {
  if (!chatSessionId.value || !runId.value) {
    error.value = '缺少会话或录制运行 ID'
    loading.value = false
    return
  }

  loading.value = true
  error.value = null
  logs.value = ['正在执行整体工作流测试...']
  try {
    const cacheKey = `recording-workflow-test:${runId.value}`
    const cached = window.sessionStorage?.getItem(cacheKey)
    if (cached) {
      window.sessionStorage?.removeItem(cacheKey)
      applyExecutionResult(JSON.parse(cached))
      return
    }

    const response = await executeWorkflowRecordingTest(chatSessionId.value, runId.value)
    applyExecutionResult(response)
  } catch (err: any) {
    success.value = false
    error.value = err.response?.data?.detail || err.message || '整体测试执行失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  runWorkflowTest()
})
</script>

<template>
  <div class="flex h-screen flex-col overflow-hidden bg-[#f5f6f7] dark:bg-[#161618]">
    <header class="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-4 dark:border-gray-800 dark:bg-gray-950">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <Play class="text-[#831bd7]" :size="18" />
          <h1 class="truncate text-lg font-extrabold text-gray-950 dark:text-gray-50">整体测试</h1>
        </div>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
          按当前 workflow 顺序执行每个 segment，并返回日志和整体输出。
        </p>
      </div>
      <button
        class="rounded-full border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 transition hover:bg-gray-50 dark:border-gray-800 dark:text-gray-300 dark:hover:bg-gray-900"
        type="button"
        @click="router.push(returnTo)"
      >
        返回对话
      </button>
    </header>

    <div class="grid min-h-0 flex-1 grid-cols-[360px_minmax(0,1fr)] gap-0">
      <aside class="overflow-y-auto border-r border-gray-200 bg-[#eff1f2] p-5 dark:border-gray-800 dark:bg-[#212122]">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-base font-extrabold text-gray-900 dark:text-gray-100">Workflow 片段</h2>
          <span class="rounded-full bg-[#831bd7]/10 px-2.5 py-1 text-[11px] font-bold text-[#831bd7]">
            {{ segments.length }} 段
          </span>
        </div>

        <div class="space-y-3">
          <div
            v-for="segment in segments"
            :key="segment.id"
            class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-[#272728]"
          >
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-xs font-semibold uppercase tracking-[0.18em] text-[#831bd7]">{{ segment.kind }}</span>
              <span class="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-400">
                {{ segment.status }}
              </span>
            </div>
            <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100">{{ segment.title }}</h3>
            <p class="mt-1 text-xs leading-6 text-gray-500 dark:text-gray-400">{{ segment.purpose }}</p>
          </div>

          <div
            v-if="!segments.length"
            class="rounded-2xl border-2 border-dashed border-gray-300 px-4 py-6 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400"
          >
            {{ loading ? '正在准备 workflow 测试...' : '当前没有可展示的片段。' }}
          </div>
        </div>
      </aside>

      <main class="overflow-y-auto p-6">
        <div
          class="rounded-3xl border p-5 shadow-sm"
          :class="loading
            ? 'border-violet-200 bg-violet-50 dark:border-violet-900/50 dark:bg-violet-950/20'
            : success
              ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-950/20'
              : 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-950/20'"
        >
          <div class="flex items-center gap-3">
            <Loader2 v-if="loading" class="animate-spin text-[#831bd7]" :size="20" />
            <CheckCircle v-else-if="success" class="text-emerald-600" :size="20" />
            <XCircle v-else class="text-rose-600" :size="20" />
            <div>
              <h2 class="text-base font-extrabold text-gray-950 dark:text-gray-50">
                {{ loading ? '整体测试执行中' : success ? '整体测试通过' : '整体测试失败' }}
              </h2>
              <p class="text-xs text-gray-500 dark:text-gray-400">
                {{ loading ? '正在顺序执行 workflow 中的每个 segment。' : success ? '已返回整体输出结果。' : '请结合日志检查失败环节。' }}
              </p>
            </div>
          </div>
          <p v-if="error" class="mt-3 text-sm text-rose-600">{{ error }}</p>
        </div>

        <section class="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div class="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-[#272728]">
            <h3 class="mb-3 text-sm font-extrabold text-gray-900 dark:text-gray-100">执行日志</h3>
            <div class="max-h-[520px] overflow-auto rounded-2xl bg-gray-950 p-4">
              <div
                v-for="(line, index) in logs"
                :key="`${index}-${line}`"
                class="font-mono text-[12px] leading-6 text-emerald-400"
              >
                <span class="mr-2 text-gray-500">{{ String(index + 1).padStart(2, '0') }}</span>{{ line }}
              </div>
              <div v-if="!logs.length" class="font-mono text-[12px] text-gray-500">等待结果...</div>
            </div>
          </div>

          <div class="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-[#272728]">
            <h3 class="mb-3 text-sm font-extrabold text-gray-900 dark:text-gray-100">整体输出</h3>
            <pre class="max-h-[520px] overflow-auto rounded-2xl bg-gray-50 p-4 text-[12px] leading-6 text-gray-700 dark:bg-gray-900 dark:text-gray-300"><code>{{ JSON.stringify(outputs, null, 2) }}</code></pre>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

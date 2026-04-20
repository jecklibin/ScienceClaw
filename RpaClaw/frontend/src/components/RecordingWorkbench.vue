<template>
  <div
    v-if="visible"
    class="fixed top-0 z-40 h-full w-full ltr:right-0 rtl:left-0 sm:sticky sm:right-0 sm:ml-3 sm:mr-4 sm:h-[100vh] sm:py-3"
    :style="{ width: `${panelWidth}px` }"
  >
    <RecorderWorkbenchShell
      class="h-full"
      :title="intent || 'Recording Workbench'"
      subtitle="对话内录制 · 共享完整录制台"
      :steps="workbenchSteps"
      :messages="assistantMessages"
      :testing-state="testingState"
      :address="addressInput"
      :tabs="tabs"
      @update:address="addressInput = $event"
      @submit-address="submitAddressBar"
      @activate-tab="activateTab"
      @canvas-event="sendInputEvent"
      @canvas-ready="setCanvas"
      @complete="completeSegment"
      @close="$emit('close')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { apiClient } from '@/api/client'
import { completeRecordingSegment } from '@/api/recording'
import RecorderWorkbenchShell from '@/components/recorder/RecorderWorkbenchShell.vue'
import type { RecordingSegmentSummary } from '@/types/recording'
import { deriveArtifactsFromRpaSteps, mapRpaStepsToRecordingSteps, type RpaStep } from '@/utils/recording'
import {
  getFrameSizeFromMetadata,
  getInputSizeFromMetadata,
  mapClientPointToViewportPoint,
  type ScreencastFrameMetadata,
  type ScreencastSize,
} from '@/utils/screencastGeometry'
import { getBackendWsUrl } from '@/utils/sandbox'

interface BrowserTab {
  tab_id: string
  title: string
  url: string
  active: boolean
}

const props = defineProps<{
  visible: boolean
  chatSessionId: string
  runId: string
  segmentId: string
  intent?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'segment-complete', payload: { segment: { id: string; status: string }; summary: RecordingSegmentSummary }): void
}>()

const panelWidth = 1080
const canvasRef = ref<HTMLCanvasElement | null>(null)
const addressInput = ref('about:blank')
const isAddressEditing = ref(false)
const tabs = ref<BrowserTab[]>([])
const rpaSessionId = ref<string | null>(null)
const rawSteps = ref<RpaStep[]>([])
const screencastFrameSize = ref<ScreencastSize>({ width: 1280, height: 720 })
const screencastInputSize = ref<ScreencastSize>({ width: 1280, height: 720 })
let screencastWs: WebSocket | null = null
let pollInterval: ReturnType<typeof setInterval> | null = null
let lastMoveTime = 0
const moveThrottle = 50

const sandboxSessionId = computed(() => `recording-${props.runId}`)
const testingState = computed(() => ({ status: 'idle' }))
const assistantMessages = computed(() => [
  {
    role: 'assistant' as const,
    text: '录制工作台已就绪。你可以直接操作浏览器，或结束本段生成步骤和产物摘要。',
    status: 'done',
  },
])
const workbenchSteps = computed(() => {
  if (!rawSteps.value.length) {
    return [{ id: '0', title: '环境就绪', description: '浏览器已启动，等待录制操作', status: 'active' }]
  }
  return [
    { id: '0', title: '环境就绪', description: '已成功启动 Playwright 浏览器', status: 'completed' },
    ...mapRpaStepsToRecordingSteps(rawSteps.value).map((step, index) => ({
      id: step.id || String(index + 1),
      title: step.description || step.action,
      description: step.description || step.action,
      status: 'completed',
      locatorSummary: step.target,
      validationStatus: step.validation?.status,
      validationDetails: step.validation?.details,
    })),
  ]
})

const setCanvas = (canvas: HTMLCanvasElement) => {
  canvasRef.value = canvas
}

const getModifiers = (event: MouseEvent | KeyboardEvent | WheelEvent): number => {
  let mask = 0
  if (event.altKey) mask |= 1
  if (event.ctrlKey) mask |= 2
  if (event.metaKey) mask |= 4
  if (event.shiftKey) mask |= 8
  return mask
}

const drawFrame = (base64Data: string, metadata: ScreencastFrameMetadata) => {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const img = new Image()
  img.onload = () => {
    const nextFrameSize = getFrameSizeFromMetadata(metadata, {
      width: img.naturalWidth,
      height: img.naturalHeight,
    })
    const nextInputSize = getInputSizeFromMetadata(metadata, nextFrameSize)
    screencastFrameSize.value = nextFrameSize
    screencastInputSize.value = nextInputSize
    if (canvas.width !== nextFrameSize.width) canvas.width = nextFrameSize.width
    if (canvas.height !== nextFrameSize.height) canvas.height = nextFrameSize.height
    ctx.drawImage(img, 0, 0)
  }
  img.src = `data:image/jpeg;base64,${base64Data}`
}

const disconnectScreencast = () => {
  if (!screencastWs) return
  screencastWs.close()
  screencastWs = null
}

const connectScreencast = (sessionId: string) => {
  if (screencastWs) return
  const wsUrl = getBackendWsUrl(`/rpa/screencast/${sessionId}`)
  const ws = new WebSocket(wsUrl)
  screencastWs = ws

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === 'frame') {
        drawFrame(msg.data, msg.metadata)
      } else if (msg.type === 'tabs_snapshot') {
        tabs.value = msg.tabs || []
        if (!isAddressEditing.value) {
          const active = tabs.value.find((tab) => tab.active)
          addressInput.value = active?.url || 'about:blank'
        }
      }
    } catch {
      // Ignore malformed websocket payloads from screencast service.
    }
  }

  ws.onclose = () => {
    if (screencastWs === ws) {
      screencastWs = null
    }
  }
}

const pollStepsOnce = async () => {
  if (!rpaSessionId.value) return
  const response = await apiClient.get(`/rpa/session/${rpaSessionId.value}`)
  rawSteps.value = response.data.session?.steps || []
}

const startPollingSteps = () => {
  if (pollInterval) clearInterval(pollInterval)
  pollInterval = setInterval(() => {
    pollStepsOnce().catch(() => {})
  }, 2500)
}

const stopPollingSteps = () => {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
}

const startSession = async () => {
  const response = await apiClient.post('/rpa/session/start', {
    sandbox_session_id: sandboxSessionId.value,
  })
  const nextSessionId = response.data.session.id as string
  rpaSessionId.value = nextSessionId
  await nextTick()
  connectScreencast(nextSessionId)
  await pollStepsOnce()
  startPollingSteps()
}

const activateTab = async (tabId: string) => {
  if (!rpaSessionId.value) return
  const response = await apiClient.post(`/rpa/session/${rpaSessionId.value}/tabs/${tabId}/activate`)
  tabs.value = response.data.tabs || []
}

const submitAddressBar = async () => {
  if (!rpaSessionId.value) return
  const response = await apiClient.post(`/rpa/session/${rpaSessionId.value}/navigate`, { url: addressInput.value })
  tabs.value = response.data.tabs || []
  addressInput.value = response.data.result?.url || addressInput.value
  isAddressEditing.value = false
  canvasRef.value?.focus()
}

const sendInputEvent = (event: Event) => {
  if (!screencastWs || screencastWs.readyState !== WebSocket.OPEN) return
  const canvas = canvasRef.value
  if (!canvas) return

  if (event instanceof MouseEvent && !(event instanceof WheelEvent)) {
    if (event.type === 'mousemove') {
      const now = Date.now()
      if (now - lastMoveTime < moveThrottle) return
      lastMoveTime = now
    }
    const rect = canvas.getBoundingClientRect()
    const point = mapClientPointToViewportPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    })
    if (!point) return
    const actionMap: Record<string, string> = {
      mousedown: 'mousePressed',
      mouseup: 'mouseReleased',
      mousemove: 'mouseMoved',
    }
    const action = actionMap[event.type]
    if (!action) return
    const buttonMap = ['left', 'middle', 'right']
    screencastWs.send(JSON.stringify({
      type: 'mouse',
      action,
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      button: buttonMap[event.button] || 'left',
      clickCount: event.type === 'mousedown' ? 1 : 0,
      modifiers: getModifiers(event),
    }))
    return
  }

  if (event instanceof WheelEvent) {
    const rect = canvas.getBoundingClientRect()
    const point = mapClientPointToViewportPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    })
    if (!point) return
    screencastWs.send(JSON.stringify({
      type: 'wheel',
      x: point.x,
      y: point.y,
      deltaX: event.deltaX,
      deltaY: event.deltaY,
      modifiers: getModifiers(event),
    }))
    return
  }

  if (event instanceof KeyboardEvent) {
    screencastWs.send(JSON.stringify({
      type: 'key',
      action: event.type === 'keydown' ? 'keyDown' : 'keyUp',
      key: event.key,
      code: event.code,
      modifiers: getModifiers(event),
      text: event.key.length === 1 ? event.key : '',
    }))
  }
}

const completeSegment = async () => {
  if (!rpaSessionId.value || !props.chatSessionId || !props.segmentId) {
    emit('close')
    return
  }

  await pollStepsOnce()
  const completed = await completeRecordingSegment(
    props.chatSessionId,
    props.runId,
    props.segmentId,
    {
      rpa_session_id: rpaSessionId.value,
      steps: mapRpaStepsToRecordingSteps(rawSteps.value),
      artifacts: deriveArtifactsFromRpaSteps(rawSteps.value),
    },
  )

  emit('segment-complete', {
    segment: completed.segment,
    summary: completed.summary as RecordingSegmentSummary,
  })
  emit('close')
}

watch(
  () => props.segmentId,
  () => {
    disconnectScreencast()
    stopPollingSteps()
    rpaSessionId.value = null
    rawSteps.value = []
    tabs.value = []
    addressInput.value = 'about:blank'
  },
)

watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      if (!rpaSessionId.value) {
        await startSession()
      } else if (!screencastWs) {
        connectScreencast(rpaSessionId.value)
      }
      startPollingSteps()
    } else {
      disconnectScreencast()
      stopPollingSteps()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disconnectScreencast()
  stopPollingSteps()
})
</script>

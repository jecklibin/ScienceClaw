import { ref } from 'vue'

export interface RecorderAssistantMessage {
  role: 'user' | 'assistant'
  text: string
  time?: string
  status?: string
}

export function useRecorderAssistant() {
  const messages = ref<RecorderAssistantMessage[]>([])
  const pendingConfirm = ref<{ description?: string; risk_reason?: string } | null>(null)
  const agentRunning = ref(false)

  return {
    messages,
    pendingConfirm,
    agentRunning,
  }
}

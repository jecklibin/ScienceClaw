import { ref } from 'vue'

export interface BrowserTab {
  tab_id: string
  title: string
  url: string
  opener_tab_id?: string | null
  status?: string
  active: boolean
}

export function useRecorderSession() {
  const sessionId = ref<string | null>(null)
  const sandboxSessionId = ref('')
  const tabs = ref<BrowserTab[]>([])
  const activeTabId = ref<string | null>(null)
  const addressInput = ref('about:blank')

  const syncTabs = (nextTabs: BrowserTab[]) => {
    tabs.value = nextTabs
    activeTabId.value = nextTabs.find((tab) => tab.active)?.tab_id || null
    addressInput.value = nextTabs.find((tab) => tab.active)?.url || addressInput.value || 'about:blank'
  }

  return {
    sessionId,
    sandboxSessionId,
    tabs,
    activeTabId,
    addressInput,
    syncTabs,
  }
}

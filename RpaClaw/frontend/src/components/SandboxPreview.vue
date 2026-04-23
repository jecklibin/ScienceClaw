<template>
  <div v-if="visible" class="flex flex-col sandbox-preview"
    :class="variant === 'inline'
      ? 'w-full rounded-xl border border-gray-200/70 dark:border-gray-700/60 bg-white dark:bg-gray-900 shadow-sm overflow-hidden'
      : ''"
    :style="rootStyle">

    <!-- Header (unified with ActivityPanel section headers) -->
    <div
      @click="expanded = !expanded"
      class="flex-shrink-0 flex items-center gap-2 cursor-pointer select-none group/sec px-4 py-2.5 border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition-colors"
      :class="variant === 'inline' ? 'bg-gray-50/80 dark:bg-gray-900/80' : ''"
    >
      <ChevronRightIcon :size="12"
        class="text-gray-400 dark:text-gray-500 transition-transform duration-150 flex-shrink-0"
        :class="{ 'rotate-90': expanded }" />
      <MonitorIcon :size="13" class="text-teal-400 flex-shrink-0" />
      <span class="text-[12px] font-semibold transition-colors"
        :class="expanded ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500 group-hover/sec:text-gray-600 dark:group-hover/sec:text-gray-300'">
        {{ variant === 'inline' ? t('Browser Preview') : t('Sandbox') }}
      </span>

      <!-- Inline tab pills -->
      <div v-if="variant !== 'inline'" class="flex items-center gap-0.5 ml-1" @click.stop>
        <button
          v-for="tab in availableTabs"
          :key="tab.id"
          @click="setActiveTab(tab.id)"
          class="px-1.5 py-0.5 text-[10px] font-medium rounded transition-colors"
          :class="activeTab === tab.id
            ? 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300'
            : 'text-gray-400 dark:text-gray-500 hover:text-gray-500 dark:hover:text-gray-400'"
        >
          {{ tab.label }}
        </button>
      </div>

      <div class="flex items-center gap-2 ml-auto">
        <!-- Live indicator -->
        <div v-if="isLive" class="flex items-center gap-1">
          <span class="relative flex h-1.5 w-1.5">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
          </span>
          <span class="text-[10px] text-emerald-600 dark:text-emerald-400 font-bold tabular-nums">LIVE</span>
        </div>

        <!-- Close button -->
        <button
          @click.stop="handleClose"
          class="flex h-5 w-5 items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md transition-colors"
        >
          <XIcon :size="11" class="text-gray-400 dark:text-gray-500" />
        </button>
      </div>
    </div>

    <!-- Content -->
    <div v-if="expanded" class="flex-1 min-h-0 overflow-hidden bg-[#1e1e1e] section-content-enter"
      :class="variant === 'inline' ? 'h-[54vh] max-h-[560px] min-h-[340px] sm:min-h-[420px]' : ''">
      <!-- Terminal view -->
      <SandboxTerminal
        v-if="activeTab === 'terminal'"
        ref="terminalRef"
        :active="expanded && activeTab === 'terminal'"
        :history="props.history"
      />

      <!-- Browser VNC view -->
      <iframe
        v-else-if="activeTab === 'browser' && !localMode && props.sessionId"
        :src="vncPageUrl"
        class="w-full h-full border-0 bg-black"
        allow="clipboard-read; clipboard-write"
      />
      <div
        v-else-if="activeTab === 'browser' && localMode"
        class="flex h-full min-h-0 flex-col bg-[#1e1e1e]"
      >
        <div
          v-if="browserTabs.length > 1"
          class="flex h-9 flex-shrink-0 items-end gap-2 overflow-x-auto bg-[#cfd3d8] px-3 dark:bg-[#2a2a2b]"
        >
          <button
            v-for="tab in browserTabs"
            :key="tab.tab_id"
            type="button"
            @click.stop="activateBrowserTab(tab.tab_id)"
            class="h-7 max-w-[220px] min-w-[120px] truncate rounded-t-xl border border-b-0 px-3 text-[11px] transition-colors"
            :class="tab.tab_id === activeBrowserTabId
              ? 'border-gray-300 bg-[#f5f6f7] text-gray-900 dark:border-gray-600 dark:bg-[#161618] dark:text-gray-100'
              : 'border-transparent bg-white/60 text-gray-600 hover:bg-white/80 dark:bg-white/10 dark:text-gray-400 dark:hover:bg-white/20'"
            :title="tab.url || tab.title"
          >
            {{ getBrowserPreviewTabLabel(tab) }}
          </button>
        </div>
        <div class="relative min-h-0 flex-1 bg-black">
          <canvas
            ref="canvasRef"
            class="h-full w-full bg-black object-contain"
          />
          <div
            v-if="previewError"
            class="absolute left-3 top-3 max-w-[calc(100%-24px)] rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-700 shadow-sm dark:border-amber-800/60 dark:bg-amber-950/80 dark:text-amber-200"
          >
            {{ previewError }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { X as XIcon, ChevronRight as ChevronRightIcon, Monitor as MonitorIcon } from 'lucide-vue-next';
import { useI18n } from 'vue-i18n';
import { apiClient } from '@/api/client';
import SandboxTerminal from './SandboxTerminal.vue';
import { getBackendVncPageUrl, getBackendWsUrl, isLocalMode, type SandboxPreviewMode } from '@/utils/sandbox';
import {
  getFrameSizeFromMetadata,
  type ScreencastFrameMetadata,
} from '@/utils/screencastGeometry';
import { getSandboxPreviewTabs, type SandboxPreviewTabId } from '@/utils/sandboxPreviewTabs';
import {
  getBrowserPreviewTabLabel,
  resolveActiveBrowserTabId,
  type BrowserPreviewTab,
} from '@/utils/browserPreviewTabs';

const { t } = useI18n();

export interface SandboxExecEntry {
  toolName: string;
  command: string;
  output?: string;
  status: string;
}

const props = defineProps<{
  mode: SandboxPreviewMode;
  isLive: boolean;
  history?: SandboxExecEntry[];
  sessionId?: string;
  variant?: 'panel' | 'inline';
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'browserEnded'): void;
}>();

const expanded = ref(true);
const activeTab = ref<SandboxPreviewTabId>(props.variant === 'inline' ? 'browser' : 'terminal');
const terminalRef = ref<InstanceType<typeof SandboxTerminal> | null>(null);
const visible = ref(false);
const localMode = ref(isLocalMode());
const canvasRef = ref<HTMLCanvasElement | null>(null);
const browserTabs = ref<BrowserPreviewTab[]>([]);
const activeBrowserTabId = ref<string | null>(null);
const previewError = ref('');
let screencastWs: WebSocket | null = null;

const vncPageUrl = computed(() => getBackendVncPageUrl(props.sessionId || 'sandbox', true));

const variant = computed(() => props.variant || 'panel');
const rootStyle = computed(() => {
  if (variant.value === 'inline') {
    return expanded.value ? { minHeight: '380px' } : { minHeight: 'auto' };
  }
  return expanded.value ? { flex: '1.5 1 0%', minHeight: '120px' } : { flex: '0 0 auto' };
});

const availableTabs = computed(() => getSandboxPreviewTabs(variant.value, props.mode));

const setActiveTab = (tab: SandboxPreviewTabId) => {
  activeTab.value = tab;
};

const drawFrame = (base64Data: string, metadata?: ScreencastFrameMetadata) => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const img = new Image();
  img.onload = () => {
    const frameSize = getFrameSizeFromMetadata(metadata, {
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    if (canvas.width !== frameSize.width) canvas.width = frameSize.width;
    if (canvas.height !== frameSize.height) canvas.height = frameSize.height;
    ctx.drawImage(img, 0, 0);
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
};

const connectScreencast = (sessionId: string) => {
  if (screencastWs) return;
  previewError.value = '';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = new URL(getBackendWsUrl('/noop')).searchParams.get('token');
  const wsUrl = new URL(`${proto}//${window.location.host}/api/v1/sessions/${sessionId}/browser/screencast`);
  if (token) {
    wsUrl.searchParams.set('token', token);
  }
  screencastWs = new WebSocket(wsUrl);

  screencastWs.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'frame') {
        previewError.value = '';
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'tabs_snapshot') {
        const nextTabs = msg.tabs || [];
        if (browserTabs.value.length > 0 && nextTabs.length === 0) {
          emit('browserEnded');
        }
        browserTabs.value = nextTabs;
        activeBrowserTabId.value = resolveActiveBrowserTabId(browserTabs.value, activeBrowserTabId.value);
      } else if (msg.type === 'preview_error') {
        previewError.value = msg.message || '';
      }
    } catch {
      // ignore malformed frames
    }
  };

  screencastWs.onclose = () => {
    screencastWs = null;
  };
};

const disconnectScreencast = () => {
  if (!screencastWs) return;
  screencastWs.close();
  screencastWs = null;
};

const activateBrowserTab = async (tabId: string) => {
  if (!props.sessionId || activeBrowserTabId.value === tabId) return;
  try {
    const resp = await apiClient.post(`/sessions/${props.sessionId}/browser/tabs/${encodeURIComponent(tabId)}/activate`);
    browserTabs.value = resp.data?.data?.tabs || browserTabs.value;
    activeBrowserTabId.value = resolveActiveBrowserTabId(browserTabs.value, tabId);
    previewError.value = '';
  } catch (err: any) {
    previewError.value = err.response?.data?.detail || err.message || '预览切换失败';
  }
};

// Auto-show if history exists on mount (panel reopen case)
if (props.history && props.history.length > 0) {
  visible.value = true;
  expanded.value = true;
}

// Auto-show based on the incoming mode, but leave connection management
// to a single watcher below so we don't flap websocket state.
watch(() => props.mode, (mode) => {
  const wasVisible = visible.value;
  if (mode === 'terminal') {
    visible.value = true;
    expanded.value = true;
    if (!wasVisible) {
      activeTab.value = 'terminal';
    }
  } else if (mode === 'browser') {
    visible.value = true;
    expanded.value = true;
    if (!wasVisible) {
      activeTab.value = 'browser';
    }
  } else if (mode === 'none') {
    visible.value = false;
    expanded.value = false;
  }
}, { immediate: true });

watch(
  () => [activeTab.value, visible.value, expanded.value, props.sessionId, localMode.value] as const,
  ([tab, isVisible, isExpanded, sessionId, isLocal]) => {
    if (tab === 'browser' && isVisible && isExpanded && isLocal && sessionId) {
      connectScreencast(sessionId);
      return;
    }
    disconnectScreencast();
  },
  { immediate: true }
);

const handleClose = () => {
  visible.value = false;
  disconnectScreencast();
  emit('close');
};

const show = (mode?: SandboxPreviewMode) => {
  visible.value = true;
  expanded.value = true;
  if (mode === 'terminal' || mode === 'browser') {
    activeTab.value = mode;
  }
};

const hide = () => {
  visible.value = false;
  disconnectScreencast();
};

const writeExecution = (toolName: string, command: string, output?: string, status?: string) => {
  terminalRef.value?.writeExecution(toolName, command, output, status);
};

onBeforeUnmount(() => {
  disconnectScreencast();
});

defineExpose({ show, hide, visible, writeExecution });
</script>

<style scoped>
.sandbox-preview {
  transition: flex 0.2s ease-out;
}

.section-content-enter {
  animation: section-reveal 0.2s ease-out;
}
@keyframes section-reveal {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>

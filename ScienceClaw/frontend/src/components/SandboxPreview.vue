<template>
  <div v-if="visible" class="flex flex-col sandbox-preview"
    :style="expanded ? { flex: '1.5 1 0%', minHeight: '120px' } : { flex: '0 0 auto' }">

    <!-- Header (unified with ActivityPanel section headers) -->
    <div
      @click="expanded = !expanded"
      class="flex-shrink-0 flex items-center gap-2 cursor-pointer select-none group/sec px-4 py-2.5 border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition-colors"
    >
      <ChevronRightIcon :size="12"
        class="text-gray-400 dark:text-gray-500 transition-transform duration-150 flex-shrink-0"
        :class="{ 'rotate-90': expanded }" />
      <MonitorIcon :size="13" class="text-teal-400 flex-shrink-0" />
      <span class="text-[12px] font-semibold transition-colors"
        :class="expanded ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500 group-hover/sec:text-gray-600 dark:group-hover/sec:text-gray-300'">
        {{ t('Sandbox') }}
      </span>

      <!-- Inline tab pills -->
      <div class="flex items-center gap-0.5 ml-1" @click.stop>
        <button
          v-for="tab in availableTabs"
          :key="tab.id"
          @click="activeTab = tab.id"
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
    <div v-if="expanded" class="flex-1 min-h-0 overflow-hidden bg-[#1e1e1e] section-content-enter">
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
      <canvas
        v-else-if="activeTab === 'browser' && localMode"
        ref="canvasRef"
        class="w-full h-full object-contain bg-black"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { X as XIcon, ChevronRight as ChevronRightIcon, Monitor as MonitorIcon } from 'lucide-vue-next';
import { useI18n } from 'vue-i18n';
import SandboxTerminal from './SandboxTerminal.vue';
import { getBackendVncPageUrl, getBackendWsUrl, isLocalMode, type SandboxPreviewMode } from '@/utils/sandbox';

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
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const expanded = ref(true);
const activeTab = ref<'terminal' | 'browser'>('browser');
const terminalRef = ref<InstanceType<typeof SandboxTerminal> | null>(null);
const visible = ref(false);
const localMode = ref(isLocalMode());
const canvasRef = ref<HTMLCanvasElement | null>(null);
let screencastWs: WebSocket | null = null;

const vncPageUrl = computed(() => getBackendVncPageUrl(props.sessionId || 'sandbox', true));

const availableTabs = computed(() => {
  const tabs: { id: 'terminal' | 'browser'; label: string }[] = [];
  tabs.push({ id: 'terminal', label: 'Terminal' });
  tabs.push({ id: 'browser', label: 'Browser' });
  return tabs;
});

const drawFrame = (base64Data: string) => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const img = new Image();
  img.onload = () => {
    if (canvas.width !== img.naturalWidth) canvas.width = img.naturalWidth;
    if (canvas.height !== img.naturalHeight) canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
};

const connectScreencast = (sessionId: string) => {
  if (screencastWs) return;
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
        drawFrame(msg.data);
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
});

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

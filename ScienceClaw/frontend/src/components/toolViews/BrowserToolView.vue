<template>
  <div
    class="h-[36px] flex items-center px-3 w-full bg-[var(--background-gray-main)] border-b border-[var(--border-main)] rounded-t-[12px] shadow-[inset_0px_1px_0px_0px_#FFFFFF] dark:shadow-[inset_0px_1px_0px_0px_#FFFFFF30]">
    <div class="flex-1 flex items-center justify-center">
      <div class="max-w-[250px] truncate text-[var(--text-tertiary)] text-sm font-medium text-center">
        {{ toolContent?.args?.url || 'Browser' }}
      </div>
    </div>
  </div>
  <div class="flex-1 min-h-0 w-full overflow-y-auto">
    <div class="px-0 py-0 flex flex-col relative h-full">
      <div class="w-full h-full object-cover flex items-center justify-center bg-[var(--fill-white)] relative">
        <div class="w-full h-full">
          <canvas
            v-if="props.live && localMode"
            ref="canvasRef"
            class="w-full h-full object-contain bg-black"
          />
          <iframe
            v-else-if="props.live && !localMode"
            :src="vncPageUrl"
            class="w-full h-full border-0 bg-black"
            allow="clipboard-read; clipboard-write"
          />
          <img
            v-else-if="imageUrl"
            alt="Image Preview"
            class="w-full h-full object-contain bg-black"
            referrerpolicy="no-referrer"
            :src="imageUrl"
          >
          <div v-else class="p-6 text-center text-sm text-[var(--text-tertiary)]">
            <div class="font-medium text-[var(--text-secondary)] mb-2">无截图可展示</div>
            <div class="break-all">{{ toolContent?.args?.url || '' }}</div>
          </div>
        </div>
        <button
          v-if="!isShare && !localMode"
          @click="takeOver"
          class="absolute right-[10px] bottom-[10px] z-10 min-w-10 h-10 flex items-center justify-center rounded-full bg-[var(--background-white-main)] text-[var(--text-primary)] border border-[var(--border-main)] shadow-[0px_5px_16px_0px_var(--shadow-S),0px_0px_1.25px_0px_var(--shadow-S)] backdrop-blur-3xl cursor-pointer hover:bg-[var(--text-brand)] hover:px-4 hover:text-[var(--text-white)] group transition-width duration-300">
          <TakeOverIcon />
          <span
            class="text-sm max-w-0 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-300 group-hover:max-w-[200px] group-hover:opacity-100 group-hover:ml-1 group-hover:text-[var(--text-white)]">{{ t('Take Over') }}</span></button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ToolContent } from '@/types/message';
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { useI18n } from 'vue-i18n';
import TakeOverIcon from '@/components/icons/TakeOverIcon.vue';
import { getBackendVncPageUrl, getBackendWsUrl, isLocalMode } from '@/utils/sandbox';

const props = defineProps<{
  sessionId: string;
  toolContent: ToolContent;
  live: boolean;
  isShare: boolean;
}>();

const { t } = useI18n();
const imageUrl = ref('');
const localMode = computed(() => isLocalMode());
const vncPageUrl = computed(() => getBackendVncPageUrl(props.sessionId || 'sandbox', true));
const canvasRef = ref<HTMLCanvasElement | null>(null);
let screencastWs: WebSocket | null = null;

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

const connectScreencast = () => {
  if (!props.live || !props.sessionId || !localMode.value || screencastWs) return;
  const wsUrl = getBackendWsUrl(`/sessions/${props.sessionId}/browser/screencast`);
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

watch(() => [props.live, props.sessionId, localMode.value], () => {
  if (props.live && props.sessionId && localMode.value) {
    connectScreencast();
    return;
  }
  disconnectScreencast();
}, { immediate: true });



watch(() => props.toolContent?.content?.screenshot, async () => {
  if (!props.toolContent?.content?.screenshot) {
    return;
  }
  imageUrl.value = props.toolContent?.content?.screenshot;
}, { immediate: true });

onBeforeUnmount(() => {
  disconnectScreencast();
});

const takeOver = () => {
  window.dispatchEvent(new CustomEvent('takeover', {
    detail: {
      sessionId: props.sessionId,
      active: true
    }
  }));
};
</script>

<style scoped>
</style>

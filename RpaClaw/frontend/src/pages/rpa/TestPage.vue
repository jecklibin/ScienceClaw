<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle,
  Code,
  FolderOpen,
  Globe,
  House,
  Loader2,
  Play,
  RotateCcw,
  Save,
  Terminal,
  XCircle,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { getBackendWsUrl } from '@/utils/sandbox';
import {
  getFrameSizeFromMetadata,
  type ScreencastFrameMetadata,
} from '@/utils/screencastGeometry';
import {
  buildScreencastReconnectMessage,
  getScreencastReconnectDelayMs,
  getScreencastReconnectNoticeDelayMs,
  isTerminalScreencastClose,
  shouldShowScreencastReconnectNotice,
} from '@/utils/screencastReconnect';
import {
  getManualRecordingDiagnostics,
  mapRpaConfigureDisplaySteps,
  type RpaRecordingDiagnosticItem,
} from '@/utils/rpaConfigureTimeline';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => route.query.sessionId as string);
const skillName = computed(() => (route.query.skillName as string) || '录制技能');
const skillDescription = computed(() => (route.query.skillDescription as string) || '');
const params = computed(() => {
  try {
    return JSON.parse((route.query.params as string) || '{}');
  } catch {
    return {};
  }
});

const canvasRef = ref<HTMLCanvasElement | null>(null);
let screencastWs: WebSocket | null = null;
let screencastReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectNoticeTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectAttempts = 0;
let screencastReconnectStartedAt = 0;
let shouldReconnectScreencast = true;

interface BrowserTab {
  tab_id: string;
  title: string;
  url: string;
  opener_tab_id?: string | null;
  status: string;
  active: boolean;
}

const tabs = ref<BrowserTab[]>([]);
const activeTabId = ref<string | null>(null);
const previewUrl = computed(() => {
  const active = tabs.value.find((tab) => tab.tab_id === activeTabId.value);
  return active?.url || 'about:blank';
});

const TEST_REQUEST_TIMEOUT_MS = 210000;

const testing = ref(false);
const testDone = ref(false);
const testSuccess = ref(false);
const testOutput = ref('');
const testLogs = ref<string[]>([]);
const generatedScript = ref('');
const recordedSteps = ref<any[]>([]);
const recordingDiagnostics = ref<RpaRecordingDiagnosticItem[]>([]);
const saving = ref(false);
const saved = ref(false);
const showScript = ref(false);
const error = ref<string | null>(null);

interface LocatorCandidate {
  kind: string;
  score: number;
  strict_match_count: number;
  visible_match_count: number;
  selected: boolean;
  locator: Record<string, any>;
  playwright_locator?: string;
  original_index?: number;
}

const failedStepIndex = ref<number | null>(null);
const failedStepCandidates = ref<LocatorCandidate[]>([]);
const failedStepError = ref('');
const triedCandidateIndices = ref<Set<number>>(new Set());
const retryingWithCandidate = ref(false);

const parseLocator = (raw: unknown) => {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return { method: 'css', value: raw };
    }
  }
  return raw as any;
};

const formatLocator = (raw: unknown): string => {
  const locator = parseLocator(raw);
  if (!locator) return 'No locator';
  if (locator.method === 'role') {
    return locator.name ? `role=${locator.role}[name="${locator.name}"]` : `role=${locator.role}`;
  }
  if (locator.method === 'nested') {
    return `${formatLocator(locator.parent)} >> ${formatLocator(locator.child)}`;
  }
  if (locator.method === 'nth') {
    const baseLocator = locator.locator || locator.base;
    const prefix = baseLocator ? `${formatLocator(baseLocator)} >> ` : '';
    return `${prefix}nth=${locator.index}`;
  }
  if (locator.method === 'css') return locator.value || 'css';
  return `${locator.method || 'locator'}:${locator.value || locator.name || ''}`;
};

const formatFramePath = (framePath?: string[]) => {
  if (!framePath?.length) return 'Main frame';
  return framePath.join(' -> ');
};

const VALIDATION_LABELS: Record<string, string> = {
  ok: 'Strict match',
  ambiguous: 'Ambiguous / not unique',
  fallback: 'Fallback',
  warning: 'Warning',
  broken: 'Broken',
};

const VALIDATION_CLASS_MAP: Record<string, string> = {
  ok: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400',
  ambiguous: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400',
  fallback: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400',
  warning: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400',
  broken: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400',
};

const getValidationLabel = (status?: string) => {
  if (!status) return 'Unknown';
  return VALIDATION_LABELS[status] || status.replace(/_/g, ' ');
};

const getValidationClass = (status?: string) => {
  if (!status) return 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300';
  return VALIDATION_CLASS_MAP[status] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300';
};

const loadSessionDiagnostics = async () => {
  if (!sessionId.value) return;
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = resp.data.session || {};
    recordedSteps.value = mapRpaConfigureDisplaySteps(session);
    recordingDiagnostics.value = getManualRecordingDiagnostics(session);
  } catch (err) {
    console.error('[TestPage] Failed to load session diagnostics:', err);
  }
};

const drawFrame = (base64Data: string, metadata: ScreencastFrameMetadata) => {
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

const clearScreencastReconnectTimer = () => {
  if (screencastReconnectTimer !== null) {
    clearTimeout(screencastReconnectTimer);
    screencastReconnectTimer = null;
  }
};

const clearScreencastReconnectNoticeTimer = () => {
  if (screencastReconnectNoticeTimer !== null) {
    clearTimeout(screencastReconnectNoticeTimer);
    screencastReconnectNoticeTimer = null;
  }
};

const hasPendingScreencastReconnect = () => (
  screencastReconnectTimer !== null ||
  (screencastWs !== null && screencastWs.readyState !== WebSocket.OPEN)
);

const disconnectScreencast = () => {
  clearScreencastReconnectTimer();
  clearScreencastReconnectNoticeTimer();
  if (!screencastWs) return;

  const ws = screencastWs;
  screencastWs = null;
  ws.onopen = null;
  ws.onmessage = null;
  ws.onerror = null;
  ws.onclose = null;

  if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
    ws.close();
  }
};

const scheduleScreencastReconnect = (sid: string) => {
  if (!shouldReconnectScreencast || screencastReconnectTimer !== null) return;

  screencastReconnectAttempts += 1;
  if (screencastReconnectStartedAt <= 0) {
    screencastReconnectStartedAt = Date.now();
  }
  const delay = getScreencastReconnectDelayMs(screencastReconnectAttempts);
  const message = buildScreencastReconnectMessage('测试', delay);
  const noticeDelay = getScreencastReconnectNoticeDelayMs({
    outageStartedAtMs: screencastReconnectStartedAt,
    nowMs: Date.now(),
  });
  clearScreencastReconnectNoticeTimer();
  screencastReconnectNoticeTimer = setTimeout(() => {
    screencastReconnectNoticeTimer = null;
    if (shouldShowScreencastReconnectNotice({
      shouldReconnect: shouldReconnectScreencast,
      hasPendingReconnect: hasPendingScreencastReconnect(),
    })) {
      error.value = message;
    }
  }, noticeDelay);
  screencastReconnectTimer = setTimeout(() => {
    screencastReconnectTimer = null;
    if (!shouldReconnectScreencast) return;
    connectScreencast(sid);
  }, delay);
};

const connectScreencast = (sid: string) => {
  clearScreencastReconnectTimer();
  if (screencastWs) {
    const existing = screencastWs;
    screencastWs = null;
    existing.onopen = null;
    existing.onmessage = null;
    existing.onerror = null;
    existing.onclose = null;
    if (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING) {
      existing.close();
    }
  }

  const wsUrl = getBackendWsUrl(`/rpa/screencast/${sid}`);
  console.log('[TestPage] Connecting screencast:', wsUrl);
  const ws = new WebSocket(wsUrl);
  screencastWs = ws;

  ws.onopen = () => {
    if (screencastWs !== ws) return;
    console.log('[TestPage] Screencast connected');
    screencastReconnectAttempts = 0;
    screencastReconnectStartedAt = 0;
    clearScreencastReconnectNoticeTimer();
    error.value = null;
  };

  ws.onmessage = (ev) => {
    if (screencastWs !== ws) return;
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'frame') {
        screencastReconnectStartedAt = 0;
        clearScreencastReconnectNoticeTimer();
        error.value = null;
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'tabs_snapshot') {
        tabs.value = msg.tabs || [];
        const active = tabs.value.find((tab) => tab.active);
        activeTabId.value = active?.tab_id || null;
      } else if (msg.type === 'preview_error') {
        error.value = msg.message || '预览切换失败';
      }
    } catch (parseError) {
      console.error('[TestPage] Parse error:', parseError);
    }
  };

  ws.onerror = (event) => {
    if (screencastWs !== ws) return;
    console.error('[TestPage] Screencast error:', event);
    if (!shouldReconnectScreencast) {
      error.value = '无法连接测试画面流，请检查后端 screencast WebSocket 或代理配置。';
    }
  };

  ws.onclose = (event) => {
    if (screencastWs !== ws) return;
    console.log('[TestPage] Screencast closed:', event.code, event.reason);
    screencastWs = null;
    if (!shouldReconnectScreencast) return;
    if (isTerminalScreencastClose(event.code)) {
      shouldReconnectScreencast = false;
      clearScreencastReconnectNoticeTimer();
      error.value = `测试画面流已断开（code=${event.code}${event.reason ? `, reason=${event.reason}` : ''}）`;
      return;
    }
    scheduleScreencastReconnect(sid);
  };
};

const activateTab = async (tabId: string) => {
  if (!sessionId.value || activeTabId.value === tabId) return;
  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/tabs/${tabId}/activate`);
    tabs.value = resp.data.tabs || [];
    const active = tabs.value.find((tab) => tab.active);
    activeTabId.value = active?.tab_id || null;
  } catch (err) {
    console.error('[TestPage] Failed to activate tab:', err);
  }
};

const runTest = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId';
    return;
  }

  if (recordingDiagnostics.value.length > 0) {
    error.value = `还有 ${recordingDiagnostics.value.length} 个待修复步骤，修复后才能开始测试`;
    testLogs.value = [`错误: 还有 ${recordingDiagnostics.value.length} 个待修复步骤，修复后才能开始测试`];
    testDone.value = true;
    testSuccess.value = false;
    return;
  }

  testing.value = true;
  testDone.value = false;
  testSuccess.value = false;
  error.value = null;
  testLogs.value = ['正在生成并执行 Playwright 脚本...'];
  const previousFailedIndex = failedStepIndex.value;
  failedStepIndex.value = null;
  failedStepCandidates.value = [];
  failedStepError.value = '';

  try {
    connectScreencast(sessionId.value);
    const testPromise = apiClient.post(
      `/rpa/session/${sessionId.value}/test`,
      { params: params.value },
      { timeout: TEST_REQUEST_TIMEOUT_MS },
    );

    const resp = await testPromise;
    const result = resp.data.result || {};
    testOutput.value = result.output || '';
    testLogs.value = resp.data.logs || [];
    generatedScript.value = resp.data.script || '';
    testSuccess.value = result.success !== false;
    const newFailedIndex = resp.data.failed_step_index ?? null;
    if (newFailedIndex !== previousFailedIndex) {
      triedCandidateIndices.value = new Set();
    }
    failedStepIndex.value = newFailedIndex;
    failedStepCandidates.value = resp.data.failed_step_candidates || [];
    failedStepError.value = result.error || '';
    testDone.value = true;
  } catch (err: any) {
    testLogs.value.push(`错误: ${err.response?.data?.detail || err.message}`);
    testSuccess.value = false;
    testDone.value = true;
  } finally {
    testing.value = false;
  }
};

const retryWithCandidate = async (candidateIndex: number) => {
  if (retryingWithCandidate.value || failedStepIndex.value === null) return;
  retryingWithCandidate.value = true;

  try {
    const candidate = failedStepCandidates.value[candidateIndex];
    const originalIndex = candidate.original_index ?? candidateIndex;
    await apiClient.post(
      `/rpa/session/${sessionId.value}/step/${failedStepIndex.value}/locator`,
      { candidate_index: originalIndex },
    );

    triedCandidateIndices.value.add(candidateIndex);
    await loadSessionDiagnostics();

    failedStepIndex.value = null;
    failedStepCandidates.value = [];
    failedStepError.value = '';
    await runTest();
  } catch (err: any) {
    error.value = `切换定位器失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    retryingWithCandidate.value = false;
  }
};

watch(failedStepIndex, (index) => {
  if (index !== null) {
    nextTick(() => {
      const el = document.querySelector(`[data-step-index="${index}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }
});

const goBackToConfigure = () => {
  router.push(`/rpa/configure?sessionId=${sessionId.value}`);
};

const goBackToRecorder = () => {
  router.push('/rpa/recorder');
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const saveSkill = async () => {
  if (!sessionId.value) return;
  saving.value = true;
  error.value = null;

  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/save`, {
      skill_name: skillName.value,
      description: skillDescription.value,
      params: params.value,
    });

    if (resp.data.status === 'success') {
      saved.value = true;
      setTimeout(() => {
        router.push('/chat/skills');
      }, 2000);
    }
  } catch (err: any) {
    error.value = `保存失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    saving.value = false;
  }
};

onMounted(() => {
  loadSessionDiagnostics().then(() => {
    if (!recordingDiagnostics.value.length) {
      runTest();
    }
  });
});

onBeforeUnmount(() => {
  shouldReconnectScreencast = false;
  disconnectScreencast();
});
</script>

<template>
  <div class="flex h-screen flex-col overflow-hidden bg-[#f5f6f7] dark:bg-[#161618]">
    <header class="flex h-14 flex-shrink-0 items-center gap-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] px-6">
      <button
        class="flex items-center gap-1 text-gray-500 dark:text-gray-400 transition-colors hover:text-gray-700 dark:hover:text-gray-200"
        @click="goBackToConfigure"
      >
        <ArrowLeft :size="18" />
      </button>
      <Play class="text-[#831bd7]" :size="22" />
      <h1 class="text-lg font-extrabold text-gray-900 dark:text-gray-100">测试技能</h1>
      <span class="max-w-48 truncate text-sm text-gray-500 dark:text-gray-400">{{ skillName }}</span>
      <div class="flex-1" />

      <button
        class="flex items-center gap-1.5 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 transition-colors hover:bg-gray-50 dark:hover:bg-[#444345] hover:text-gray-900 dark:hover:text-white"
        @click="goToHome"
      >
        <House :size="15" />
        返回首页
      </button>
      <button
        class="flex items-center gap-1.5 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 transition-colors hover:bg-gray-50 dark:hover:bg-[#444345] hover:text-gray-900 dark:hover:text-white"
        @click="goToSkills"
      >
        <FolderOpen :size="15" />
        技能库
      </button>

      <div
        v-if="saved"
        class="flex items-center gap-2 text-sm font-bold text-green-600"
      >
        <CheckCircle :size="18" />
        技能已保存，正在跳转...
      </div>
    </header>

    <div class="flex min-h-0 flex-1">
      <aside class="flex w-[300px] flex-col overflow-y-auto border-r border-gray-200 dark:border-gray-700 bg-[#eff1f2] dark:bg-[#212122] p-5">
        <div class="mb-6 flex items-center justify-between">
          <h2 class="text-lg font-extrabold text-gray-900 dark:text-gray-100">录制步骤</h2>
          <span class="rounded-md bg-[#c384ff]/20 px-2 py-1 text-[10px] font-bold text-[#831bd7]">
            {{ recordedSteps.length }} 步
          </span>
        </div>

        <div
          v-if="recordingDiagnostics.length"
          class="mb-4 rounded-xl border border-rose-200 dark:border-rose-900/60 bg-rose-50/80 dark:bg-rose-950/20 p-3 text-xs text-rose-700 dark:text-rose-300"
        >
          <p class="font-semibold">还有 {{ recordingDiagnostics.length }} 个待修复步骤</p>
          <p class="mt-1">请先回到配置页修复或删除这些步骤，然后再开始测试。</p>
        </div>

        <div class="space-y-4">
          <div
            v-for="(step, index) in recordedSteps"
            :key="step.id || index"
            :data-step-index="index"
            class="rounded-xl border-l-4 bg-white dark:bg-[#272728] p-4 shadow-sm"
            :class="[ failedStepIndex === index ? 'border-red-500 ring-2 ring-red-100' : 'border-gray-200 dark:border-gray-700' ]"
          >
            <div class="mb-1 flex items-center justify-between gap-3">
              <span class="text-[10px] font-bold text-gray-400 dark:text-gray-500">
                步骤 {{ String(index + 1).padStart(2, '0') }}
              </span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                :class="getValidationClass(step.validation?.status)"
              >
                {{ getValidationLabel(step.validation?.status) }}
              </span>
            </div>
            <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {{ step.description || step.action }}
            </h3>
            <p class="mt-2 break-all text-[11px] text-gray-500 dark:text-gray-400">
              <span class="font-semibold text-gray-600 dark:text-gray-400">Locator:</span>
              <span class="ml-1 font-mono">{{ formatLocator(step.target) }}</span>
            </p>
            <p class="mt-1 break-all text-[11px] text-gray-500 dark:text-gray-400">
              <span class="font-semibold text-gray-600 dark:text-gray-400">Frame:</span>
              <span class="ml-1 font-mono">{{ formatFramePath(step.frame_path) }}</span>
            </p>
            <p
              v-if="step.validation?.details"
              class="mt-1 break-all text-[11px] text-gray-500 dark:text-gray-400"
            >
              <span class="font-semibold text-gray-600 dark:text-gray-400">Details:</span>
              <span class="ml-1">{{ step.validation.details }}</span>
            </p>

            <div v-if="failedStepIndex === index" class="mt-3 space-y-3">
              <div class="rounded-lg bg-red-50 dark:bg-red-900/30 p-2.5 text-[11px] text-red-700">
                <p class="font-semibold flex items-center gap-1">
                  <AlertTriangle :size="12" />
                  执行失败
                </p>
                <p class="mt-1 break-all">{{ failedStepError }}</p>
              </div>

              <div v-if="failedStepCandidates.length > 0">
                <p class="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  尝试其他定位器：
                </p>
                <div class="mt-2 space-y-1.5">
                  <button
                    v-for="(candidate, cIdx) in failedStepCandidates"
                    :key="cIdx"
                    class="w-full rounded-lg border p-2 text-left text-[11px] transition-colors"
                    :class="[ triedCandidateIndices.has(cIdx) ? 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-[#383739] opacity-60' : 'border-purple-200 dark:border-purple-800 hover:bg-purple-50 hover:border-purple-400' ]"
                    :disabled="retryingWithCandidate"
                    @click="retryWithCandidate(cIdx)"
                  >
                    <div class="flex items-center justify-between">
                      <span class="font-mono font-medium text-gray-900 dark:text-gray-100 truncate">
                        {{ candidate.kind }}: {{ candidate.playwright_locator || formatLocator(candidate.locator) }}
                      </span>
                      <span
                        v-if="cIdx === 0 && !triedCandidateIndices.has(cIdx)"
                        class="flex-shrink-0 rounded-full bg-purple-100 px-1.5 py-0.5 text-[9px] font-bold text-purple-700"
                      >
                        推荐
                      </span>
                      <span
                        v-if="triedCandidateIndices.has(cIdx)"
                        class="flex-shrink-0 rounded-full bg-gray-200 px-1.5 py-0.5 text-[9px] font-bold text-gray-500 dark:text-gray-400"
                      >
                        已尝试
                      </span>
                    </div>
                    <div class="mt-1 flex gap-3 text-gray-500 dark:text-gray-400">
                      <span>score: {{ candidate.score }}</span>
                      <span :class="candidate.strict_match_count === 1 ? 'text-green-600' : 'text-amber-600'">
                        match: {{ candidate.strict_match_count }}
                        {{ candidate.strict_match_count === 1 ? '\u2713' : '\u26A0' }}
                      </span>
                    </div>
                  </button>
                </div>
              </div>

              <div v-else class="rounded-lg bg-amber-50 dark:bg-amber-900/30 p-2.5 text-[11px] text-amber-700">
                此步骤无候选定位器可切换，建议返回重新录制。
              </div>
            </div>
          </div>

          <div
            v-if="!recordedSteps.length"
            class="flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-gray-300 dark:border-gray-600 py-8 opacity-60"
          >
            <div class="animate-spin text-[#831bd7]">
              <Loader2 :size="20" />
            </div>
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400">等待录制步骤加载...</p>
          </div>
        </div>
      </aside>

      <main class="flex min-w-0 flex-1 flex-col bg-[#f5f6f7] dark:bg-[#161618] px-5 py-4">
        <div class="relative flex flex-1 flex-col overflow-hidden rounded-2xl border border-gray-800 dark:border-gray-600 bg-[#1e1e1e] shadow-2xl">
          <div class="flex h-9 flex-shrink-0 items-end gap-2 overflow-x-auto bg-[#cfd3d8] dark:bg-[#2a2a2b] px-3">
            <button
              v-for="tab in tabs"
              :key="tab.tab_id"
              class="h-7 max-w-[220px] min-w-[120px] truncate rounded-t-xl border border-b-0 px-3 text-[11px] transition-colors"
              :class=" tab.active ? 'border-gray-300 dark:border-gray-600 bg-[#f5f6f7] dark:bg-[#161618] text-gray-900 dark:text-gray-100' : 'border-transparent bg-white/60 dark:bg-white/10 text-gray-600 dark:text-gray-400 hover:bg-white/80 dark:hover:bg-white/20' "
              type="button"
              @click="activateTab(tab.tab_id)"
            >
              {{ tab.title || tab.url || 'New Tab' }}
            </button>
          </div>

          <div class="flex h-9 flex-shrink-0 items-center gap-2 border-t border-white/40 dark:border-white/10 bg-[#dadddf] dark:bg-[#383839] px-3">
            <div class="flex gap-1.5">
              <div class="h-2.5 w-2.5 rounded-full bg-red-400" />
              <div class="h-2.5 w-2.5 rounded-full bg-yellow-400" />
              <div class="h-2.5 w-2.5 rounded-full bg-green-400" />
            </div>
            <div class="mx-3 flex h-5 flex-1 items-center rounded-md border border-transparent bg-white dark:bg-[#272728] px-2 shadow-inner">
              <Globe class="flex-shrink-0 text-gray-400 dark:text-gray-500" :size="12" />
              <input
                :value="previewUrl"
                class="ml-2 flex-1 bg-transparent text-[10px] text-gray-700 dark:text-gray-300 outline-none"
                readonly
                spellcheck="false"
                type="text"
              />
            </div>
          </div>

          <div class="relative flex-1 overflow-hidden bg-black">
            <canvas
              ref="canvasRef"
              class="h-full w-full object-contain"
            />

            <div
              v-if="error"
              class="absolute right-4 top-4 max-w-xs rounded-lg bg-red-500/90 px-3 py-2 text-[11px] text-white shadow-lg"
            >
              {{ error }}
            </div>

            <div
              v-if="sessionId"
              class="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1.5 backdrop-blur-md"
            >
              <Loader2
                v-if="testing"
                class="animate-spin text-[#831bd7]"
                :size="14"
              />
              <Play
                v-else
                class="text-emerald-400"
                :size="14"
              />
              <span class="text-[10px] font-bold uppercase tracking-wider text-white">
                {{ testing ? '测试执行中' : '测试回放预览' }}
              </span>
            </div>
          </div>
        </div>
      </main>

      <aside class="flex w-[320px] flex-shrink-0 flex-col overflow-hidden border-l border-gray-200 dark:border-gray-700 bg-[#eff1f2] dark:bg-[#212122] shadow-[-10px_0_40px_-10px_rgba(0,0,0,0.03)]">
        <div class="flex-1 space-y-4 overflow-y-auto p-4">
          <div
            class="rounded-xl border p-4"
            :class="[ testing ? 'border-purple-200 dark:border-purple-800 bg-purple-50 dark:bg-purple-900/30' : testDone && testSuccess ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/30' : testDone && !testSuccess ? 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/30' : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-[#383739]', ]"
          >
            <div class="mb-3 flex items-center gap-3">
              <Loader2
                v-if="testing"
                class="animate-spin text-[#831bd7]"
                :size="20"
              />
              <CheckCircle
                v-else-if="testDone && testSuccess"
                class="text-green-500"
                :size="20"
              />
              <XCircle
                v-else-if="testDone && !testSuccess"
                class="text-red-500"
                :size="20"
              />
              <div
                v-else
                class="h-5 w-5 rounded-full bg-gray-300"
              />
              <h2 class="text-base font-bold text-gray-900 dark:text-gray-100">
                {{ testing ? '正在执行...' : testDone ? (testSuccess ? '执行成功' : '执行失败') : '准备测试' }}
              </h2>
            </div>
            <p
              v-if="testDone && testSuccess"
              class="text-xs leading-relaxed text-green-700"
            >
              脚本已成功执行，可以保存为技能或重新执行验证。
            </p>
            <p
              v-if="testDone && !testSuccess && failedStepIndex !== null && failedStepCandidates.length > 0"
              class="text-xs leading-relaxed text-red-700"
            >
              步骤 {{ (failedStepIndex ?? 0) + 1 }} 执行失败，左侧已展示候选定位器，请选择一个后自动重试。
            </p>
            <p
              v-else-if="testDone && !testSuccess"
              class="text-xs leading-relaxed text-red-700"
            >
              执行过程中出现错误，请查看日志后重新执行或返回修改。
            </p>
          </div>

          <div
            v-if="testDone && !saved"
            class="flex flex-col gap-2.5"
          >
            <button
              class="flex w-full items-center justify-center gap-2 rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-[#272728] px-4 py-2.5 text-sm font-medium transition-colors hover:bg-gray-50 dark:hover:bg-[#444345] disabled:opacity-50"
              :disabled="testing || recordingDiagnostics.length > 0"
              @click="runTest"
            >
              <RotateCcw :size="15" />
              重新执行
            </button>
            <button
              class="flex w-full items-center justify-center gap-2 rounded-xl bg-[#831bd7] px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-[#7018b8] disabled:opacity-50"
              :disabled="saving"
              @click="saveSkill"
            >
              <Save :size="15" />
              {{ saving ? '保存中...' : '保存技能' }}
            </button>
            <button
              class="flex w-full items-center justify-center gap-2 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 transition-colors hover:text-gray-700 dark:hover:text-gray-200"
              @click="goBackToRecorder"
            >
              重新录制
            </button>
          </div>

          <div
            v-if="error"
            class="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/30 p-3"
          >
            <p class="text-xs text-red-600">{{ error }}</p>
          </div>

          <div>
            <h3 class="mb-2 flex items-center gap-1.5 text-sm font-bold text-gray-900 dark:text-gray-100">
              <Terminal class="text-gray-500 dark:text-gray-400" :size="14" />
              执行日志
            </h3>
            <div class="max-h-60 overflow-y-auto rounded-lg bg-gray-900 dark:bg-gray-800 p-3">
              <div
                v-for="(log, idx) in testLogs"
                :key="idx"
                class="font-mono text-[11px] leading-relaxed text-green-400"
              >
                <span class="mr-1.5 text-gray-600 dark:text-gray-400">{{ String(idx + 1).padStart(2, '0') }}</span>
                {{ log }}
              </div>
              <div
                v-if="testOutput"
                class="mt-2 border-t border-gray-700 pt-2 font-mono text-[11px] text-gray-400 dark:text-gray-500"
              >
                {{ testOutput }}
              </div>
              <div
                v-if="!testLogs.length && !testOutput"
                class="font-mono text-[11px] text-gray-600 dark:text-gray-400"
              >
                等待执行...
              </div>
            </div>
          </div>

          <div v-if="generatedScript">
            <button
              class="mb-2 flex items-center gap-1.5 text-sm font-bold text-gray-900 dark:text-gray-100 transition-colors hover:text-[#831bd7]"
              @click="showScript = !showScript"
            >
              <Code class="text-gray-500 dark:text-gray-400" :size="14" />
              {{ showScript ? '收起脚本' : '查看脚本' }}
            </button>
            <pre
              v-if="showScript"
              class="max-h-64 overflow-x-auto overflow-y-auto rounded-lg bg-gray-900 dark:bg-gray-800 p-3 text-[11px] text-green-400"
            ><code>{{ generatedScript }}</code></pre>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

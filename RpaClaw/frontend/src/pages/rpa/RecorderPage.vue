<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Camera, Terminal, CheckCircle, Radio, Send, Wand2, Bot, Code, Globe, AlertCircle, ChevronDown, ChevronUp, ClipboardCheck, Loader2 } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { listModels, type ModelConfig } from '@/api/models';
import ProviderIcon from '@/components/icons/ProviderIcon.vue';
import RpaFlowGuide from '@/components/rpa/RpaFlowGuide.vue';
import RpaStepTimeline from '@/components/rpa/RpaStepTimeline.vue';
import { getBackendWsUrl } from '@/utils/sandbox';
import {
  getFrameSizeFromMetadata,
  getInputSizeFromMetadata,
  mapClientPointToViewportPoint,
  type ScreencastFrameMetadata,
  type ScreencastSize,
} from '@/utils/screencastGeometry';
import {
  buildScreencastReconnectMessage,
  getScreencastReconnectDelayMs,
  getScreencastReconnectNoticeDelayMs,
  isTerminalScreencastClose,
  shouldShowScreencastReconnectNotice,
} from '@/utils/screencastReconnect';
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';
import {
  getInitialRpaAgentProgress,
  getRpaAgentProgressForEvent,
  type RpaAgentMessageStatus,
} from '@/utils/rpaAgentProgress';
import {
  applyRpaAssistantRunEvent,
  createRpaAssistantRun,
  type RpaAssistantRun,
  type RpaAssistantRunItem,
  type RpaAssistantRound,
} from '@/utils/rpaAssistantRun';
import {
  getManualRecordingDiagnostics,
  isRpaTimelineStepDeletable,
  mapRpaConfigureDisplaySteps,
} from '@/utils/rpaConfigureTimeline';
import {
  buildRpaAssistantChatPayload,
  getDefaultRpaAssistantModelId,
  shouldSubmitRpaAssistantComposer,
} from '@/utils/rpaAssistantModel';
import { buildRpaAssistantRequestHeaders } from '@/utils/rpaAssistantRequest';

const router = useRouter();
const route = useRoute();
const launchSource = ref(typeof route.query.source === 'string' ? route.query.source : '');

const sessionId = ref<string | null>(null);
const sandboxSessionId = ref<string>('');
const isRecording = ref(true);
const recordingTime = ref('00:00');
const timerInterval = ref<any>(null);
const loading = ref(true);
const error = ref<string | null>(null);

const canvasRef = ref<HTMLCanvasElement | null>(null);
const screencastFrameSize = ref<ScreencastSize>({ width: 1280, height: 720 });
const screencastInputSize = ref<ScreencastSize>({ width: 1280, height: 720 });
let screencastWs: WebSocket | null = null;
let screencastReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectNoticeTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectAttempts = 0;
let screencastReconnectStartedAt = 0;
let shouldReconnectScreencast = true;
let currentScreencastSessionId: string | null = null;
let lastMoveTime = 0;
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
const addressInput = ref('about:blank');
const isAddressEditing = ref(false);
const isNavigating = ref(false);
const MOVE_THROTTLE = 50; // 50ms 节流

const steps = ref<any[]>([
  { id: '0', title: '初始化环境', description: '正在配置沙箱录制环境...', status: 'active' }
]);
const acceptedTraces = ref<any[]>([]);
const recordingDiagnostics = ref<any[]>([]);

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

const mapServerSteps = (serverSteps: any[]) => ([
  { id: '0', title: '环境就绪', description: '已成功启动 Playwright 浏览器', status: 'completed' },
  ...serverSteps.map((s: any, i: number) => ({
    id: String(i + 1),
    title: s.description || s.action,
    description: s.source === 'ai' ? (s.prompt || s.description || 'AI 操作') : `${s.action} -> ${formatLocator(s.target || s.label || '')}`,
    status: 'completed',
    source: s.source || 'record',
    sensitive: s.sensitive || false,
    locatorSummary: formatLocator(s.target),
    frameSummary: formatFramePath(s.frame_path),
    validationStatus: s.validation?.status || '',
    validationDetails: s.validation?.details || '',
  }))
]);

const formatTraceType = (traceType?: string) => {
  const value = traceType || '';
  if (value === 'ai_operation') return 'AI Trace';
  if (value === 'data_capture') return 'Data Capture';
  if (value === 'dataflow_fill') return 'Dataflow Fill';
  if (value === 'navigation') return 'Navigation';
  if (value === 'manual_action') return 'Manual';
  return value || 'Trace';
};

const mapServerTraces = (serverTraces: any[]) => ([
  { id: '0', title: 'Environment ready', description: 'Playwright browser is ready', status: 'completed', deletable: false },
  ...serverTraces.map((t: any, i: number) => ({
    id: String(i + 1),
    traceId: t.trace_id || '',
    title: t.description || t.user_instruction || formatTraceType(t.trace_type),
    description: t.user_instruction || t.action || formatTraceType(t.trace_type),
    status: 'completed',
    source: t.source === 'ai' || t.trace_type === 'ai_operation' ? 'ai' : 'record',
    traceType: t.trace_type,
    sensitive: false,
    deletable: isRpaTimelineStepDeletable({
      source: t.source === 'ai' || t.trace_type === 'ai_operation' ? 'ai' : 'record',
      traceId: t.trace_id || '',
    }),
    locatorSummary: t.locator_candidates?.length ? formatLocator(t.locator_candidates[0]?.locator || t.locator_candidates[0]) : '',
    frameSummary: t.after_page?.url || '',
    validationStatus: t.accepted === false ? 'warning' : 'ok',
    validationDetails: formatTraceType(t.trace_type),
  }))
]);

const mapConfigureTimelineSteps = (session: any) => ([
  { id: '0', title: 'Environment ready', description: 'Playwright browser is ready', status: 'completed', deletable: false },
  ...mapRpaConfigureDisplaySteps(session).map((step: any, index: number) => ({
    id: String(index + 1),
    stepId: step.stepId || '',
    traceId: step.traceId || '',
    diagnosticId: step.diagnosticId || '',
    title: step.description || step.action,
    description: step.description || step.action,
    status: 'completed',
    source: step.source || 'record',
    sensitive: step.sensitive || false,
    deletable: isRpaTimelineStepDeletable({ source: step.source || 'record', traceId: step.traceId || '' }),
    locatorSummary: formatLocator(step.target),
    frameSummary: formatFramePath(step.frame_path),
    validationStatus: step.validation?.status || '',
    validationDetails: step.validation?.details || '',
  })),
]);

const refreshTimeline = (session: any) => {
  const serverSteps = Array.isArray(session?.steps) ? session.steps : [];
  const serverTraces = Array.isArray(session?.traces) ? session.traces : [];
  const serverTimeline = Array.isArray(session?.timeline) ? session.timeline : [];
  acceptedTraces.value = serverTraces;
  recordingDiagnostics.value = getManualRecordingDiagnostics(session);
  if (serverTimeline.length > 0 || (Array.isArray(session?.recorded_actions) && session.recorded_actions.length > 0) || serverTraces.length > 0) {
    steps.value = mapConfigureTimelineSteps(session);
    return;
  }
  if (serverSteps.length > 0) {
    steps.value = mapServerSteps(serverSteps);
  }
};

const sessionWithTimeline = (payload: any) => ({
  ...(payload?.session || {}),
  timeline: payload?.session?.timeline ?? payload?.timeline,
});

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  time: string;
  script?: string;
  status?: RpaAgentMessageStatus;
  processingLabel?: string;
  error?: string;
  showCode?: boolean;
  actions?: Array<{ description: string; code: string; showCode?: boolean }>;  // Track agent actions
  run?: RpaAssistantRun;
  frameSummary?: string;
  locatorSummary?: string;
  collectionSummary?: string;
  diagnostics?: string[];
}

const chatMessages = ref<ChatMessage[]>([]);
const newMessage = ref('');
const sending = ref(false);
const agentRunning = ref(false);
const chatScrollRef = ref<HTMLElement | null>(null);
const models = ref<ModelConfig[]>([]);
const selectedModelId = ref<string | null>(null);
const modelDropdownOpen = ref(false);

const selectedModel = computed(() => (
  models.value.find((model) => model.id === selectedModelId.value) ?? null
));

const modelDisplayName = (model: ModelConfig) => (
  model.name.toLowerCase() === 'system' ? model.model_name : model.name
);

const selectedModelName = computed(() => (
  selectedModel.value ? modelDisplayName(selectedModel.value) : 'Select Model'
));

const loadAssistantModels = async () => {
  try {
    const modelList = await listModels();
    models.value = modelList;
    selectedModelId.value = getDefaultRpaAssistantModelId(modelList, selectedModelId.value);
  } catch (err) {
    console.error('Failed to load RPA assistant models:', err);
    models.value = [];
    selectedModelId.value = null;
  }
};

const selectAssistantModel = (modelId: string) => {
  selectedModelId.value = modelId;
  modelDropdownOpen.value = false;
};

const scrollAssistantToBottom = () => {
  void nextTick(() => {
    const container = chatScrollRef.value;
    if (!container) return;
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  });
};

const runRoundCount = (run?: RpaAssistantRun) => run?.rounds.length || 0;

const getRunTraceCount = (msg: ChatMessage) => (
  msg.run?.traceCount || Math.max(acceptedTraces.value.length, 0)
);

const getRunStatusLabel = (msg: ChatMessage) => {
  if (msg.status === 'error') return '未完成';
  if (msg.status === 'done') return '已完成';
  if (msg.processingLabel) return '处理中';
  return '准备中';
};

const getRunStatusClass = (msg: ChatMessage) => {
  if (msg.status === 'error') return 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300';
  if (msg.status === 'done') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
  return 'bg-[#f0dbff] text-[#6900b3] dark:bg-[#831bd7]/20 dark:text-purple-200';
};

const getRoundStatusLabel = (round: RpaAssistantRound) => {
  if (round.status === 'error') return '需要修复';
  if (round.status === 'done') return '已接收';
  return '处理中';
};

const getRoundStatusClass = (round: RpaAssistantRound) => {
  if (round.status === 'error') return 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300';
  if (round.status === 'done') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
  return 'bg-[#edeef0] text-gray-700 dark:bg-white/10 dark:text-gray-300';
};

const getRunItemToneClass = (item: RpaAssistantRunItem) => {
  if (item.kind === 'diagnostic') return 'bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-200';
  if (item.kind === 'trace') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200';
  if (item.kind === 'action') return 'bg-[#f6f0ff] text-[#4f00d0] dark:bg-[#831bd7]/15 dark:text-purple-200';
  return 'bg-[#f2f4f6] text-gray-700 dark:bg-white/[0.08] dark:text-gray-300';
};

interface PendingConfirm {
  description: string;
  risk_reason: string;
  code: string;
}
const pendingConfirm = ref<PendingConfirm | null>(null);
let pollInterval: any = null;

const syncAddressBar = (force = false) => {
  if (isAddressEditing.value && !force) return;
  const active = tabs.value.find((tab) => tab.tab_id === activeTabId.value);
  addressInput.value = active?.url || 'about:blank';
};

const syncTabs = (nextTabs: BrowserTab[]) => {
  tabs.value = nextTabs;
  const active = nextTabs.find((tab) => tab.active);
  activeTabId.value = active?.tab_id || null;
  syncAddressBar();
};

const cleanupAssistantText = (text: string, script = '') => {
  let next = text;
  next = next.replace(/^正在分析当前页面\.\.\.\s*/u, '');
  next = next.replace(/```python[\s\S]*?```/gu, '');
  if (script) {
    next = next.replace(script, '');
  }
  return next.trim();
};

const formatAgentDiagnostics = (diagnostics: any[] = []) => diagnostics
  .map((item: any) => {
    const message = String(item?.message || '').trim();
    const rawError = item?.raw?.result?.error ? String(item.raw.result.error).trim() : '';
    if (message && rawError && rawError !== message) return `${message}: ${rawError}`;
    return message || rawError;
  })
  .filter(Boolean);

const initSession = async () => {
  try {
    loading.value = true;
    const sandboxId =
      (route.query.sandboxId as string) ||
      (typeof crypto !== 'undefined' && crypto.randomUUID
        ? `rpa-${crypto.randomUUID()}`
        : `rpa-${Date.now()}`);
    sandboxSessionId.value = sandboxId;

    const resp = await apiClient.post('/rpa/session/start', {
      sandbox_session_id: sandboxId
    });

    if (resp.data.status === 'success') {
      sessionId.value = resp.data.session.id;
      steps.value = [
        { id: '0', title: '环境就绪', description: '已成功启动 Playwright 浏览器，准备录制', status: 'completed' }
      ];
      startTimer();
      startPollingSteps();
      await nextTick();
      connectScreencast(resp.data.session.id);
    }
  } catch (err: any) {
    console.error('Failed to start RPA session:', err);
    error.value = '无法启动录制会话，请检查后端服务。';
  } finally {
    loading.value = false;
  }
};

const startPollingSteps = () => {
  pollInterval = setInterval(async () => {
    if (!sessionId.value) return;
    try {
      const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
      refreshTimeline(sessionWithTimeline(resp.data));
    } catch (err) {
      // Ignore polling errors
    }
  }, 3000);
};

const startTimer = () => {
  let seconds = 0;
  timerInterval.value = setInterval(() => {
    seconds++;
    const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
    const secs = (seconds % 60).toString().padStart(2, '0');
    recordingTime.value = `${mins}:${secs}`;
  }, 1000);
};

onMounted(() => {
  loadAssistantModels();
  initSession();
});

onBeforeUnmount(() => {
  shouldReconnectScreencast = false;
  if (timerInterval.value) clearInterval(timerInterval.value);
  if (pollInterval) clearInterval(pollInterval);
  disconnectScreencast();
});

const getModifiers = (e: MouseEvent | KeyboardEvent | WheelEvent): number => {
  let mask = 0;
  if (e.altKey) mask |= 1;
  if (e.ctrlKey) mask |= 2;
  if (e.metaKey) mask |= 4;
  if (e.shiftKey) mask |= 8;
  return mask;
};

const drawFrame = (base64Data: string, metadata: ScreencastFrameMetadata) => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const img = new Image();
  img.onload = () => {
    const nextFrameSize = getFrameSizeFromMetadata(metadata, {
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    const nextInputSize = getInputSizeFromMetadata(metadata, nextFrameSize);
    screencastFrameSize.value = nextFrameSize;
    screencastInputSize.value = nextInputSize;

    if (canvas.width !== nextFrameSize.width) canvas.width = nextFrameSize.width;
    if (canvas.height !== nextFrameSize.height) canvas.height = nextFrameSize.height;
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

const scheduleScreencastReconnect = () => {
  if (!shouldReconnectScreencast || !currentScreencastSessionId || screencastReconnectTimer !== null) return;

  screencastReconnectAttempts += 1;
  if (screencastReconnectStartedAt <= 0) {
    screencastReconnectStartedAt = Date.now();
  }
  const delay = getScreencastReconnectDelayMs(screencastReconnectAttempts);
  const message = buildScreencastReconnectMessage('录制', delay);
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
    if (!shouldReconnectScreencast || !currentScreencastSessionId) return;
    connectScreencast(currentScreencastSessionId);
  }, delay);
};

const connectScreencast = (sid: string) => {
  currentScreencastSessionId = sid;
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
  console.log('[RecorderPage] Connecting screencast:', wsUrl);
  const ws = new WebSocket(wsUrl);
  screencastWs = ws;

  ws.onopen = () => {
    if (screencastWs !== ws) return;
    console.log('[RecorderPage] Screencast connected');
    screencastReconnectAttempts = 0;
    screencastReconnectStartedAt = 0;
    clearScreencastReconnectNoticeTimer();
    error.value = null;
  };

  ws.onmessage = (ev) => {
    if (screencastWs !== ws) return;
    try {
      const msg = JSON.parse(ev.data);
      console.log('[RecorderPage] Screencast message:', msg.type, msg.message || '');
      if (msg.type === 'frame') {
        screencastReconnectStartedAt = 0;
        clearScreencastReconnectNoticeTimer();
        error.value = null;
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'tabs_snapshot') {
        syncTabs(msg.tabs || []);
      } else if (msg.type === 'preview_error') {
        error.value = msg.message || '预览切换失败';
      }
    } catch (parseError) {
      console.error('[RecorderPage] Screencast parse error:', parseError);
    }
  };

  ws.onclose = (ev) => {
    if (screencastWs !== ws) return;
    console.warn('[RecorderPage] Screencast closed:', ev.code, ev.reason);
    screencastWs = null;
    if (!shouldReconnectScreencast) return;
    if (isTerminalScreencastClose(ev.code)) {
      shouldReconnectScreencast = false;
      clearScreencastReconnectNoticeTimer();
      error.value = `录制画面流已断开（code=${ev.code}${ev.reason ? `, reason=${ev.reason}` : ''}）`;
      return;
    }
    scheduleScreencastReconnect();
  };

  ws.onerror = (ev) => {
    if (screencastWs !== ws) return;
    console.error('[RecorderPage] Screencast error:', ev);
    if (!shouldReconnectScreencast) {
      error.value = '无法连接录制画面流，请检查后端 screencast WebSocket/代理配置。';
    }
  };
};

const activateTab = async (tabId: string) => {
  if (!sessionId.value || activeTabId.value === tabId) return;
  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/tabs/${tabId}/activate`);
    syncTabs(resp.data.tabs || []);
  } catch (err) {
    console.error('Failed to activate RPA tab:', err);
  }
};

const submitAddressBar = async () => {
  if (!sessionId.value || isNavigating.value) return;
  const rawUrl = addressInput.value.trim();
  if (!rawUrl) {
    syncAddressBar(true);
    return;
  }

  isNavigating.value = true;
  error.value = null;
  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/navigate`, { url: rawUrl });
    syncTabs(resp.data.tabs || []);
    addressInput.value = resp.data.result?.url || addressInput.value;
    focusCanvas();
  } catch (err: any) {
    console.error('Failed to navigate active RPA tab:', err);
    error.value = err.response?.data?.detail || '地址栏导航失败';
  } finally {
    isNavigating.value = false;
    isAddressEditing.value = false;
  }
};

const handleAddressBlur = () => {
  isAddressEditing.value = false;
  syncAddressBar();
};

const focusCanvas = () => {
  canvasRef.value?.focus();
};

const sendInputEvent = (e: Event) => {
  if (!screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  const canvas = canvasRef.value;
  if (!canvas) return;

  if (e instanceof MouseEvent && !(e instanceof WheelEvent)) {
    if (e.type === 'mousemove') {
      const now = Date.now();
      if (now - lastMoveTime < MOVE_THROTTLE) return;
      lastMoveTime = now;
    }

    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    const actionMap: Record<string, string> = {
      mousedown: 'mousePressed',
      mouseup: 'mouseReleased',
      mousemove: 'mouseMoved',
    };
    const action = actionMap[e.type];
    if (!action) return;
    const buttonMap = ['left', 'middle', 'right'];
    screencastWs.send(JSON.stringify({
      type: 'mouse',
      action,
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      button: buttonMap[e.button] || 'left',
      clickCount: e.type === 'mousedown' ? 1 : 0,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof WheelEvent) {
    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    screencastWs.send(JSON.stringify({
      type: 'wheel',
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      deltaX: e.deltaX,
      deltaY: e.deltaY,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof KeyboardEvent) {
    const action = e.type === 'keydown' ? 'keyDown' : 'keyUp';
    screencastWs.send(JSON.stringify({
      type: 'keyboard',
      action,
      key: e.key,
      code: e.code,
      text: e.type === 'keydown' && e.key.length === 1 ? e.key : '',
      modifiers: getModifiers(e),
    }));
  }
};

const stopRecording = async () => {
  isRecording.value = false;
  if (timerInterval.value) clearInterval(timerInterval.value);
  if (pollInterval) clearInterval(pollInterval);

  if (sessionId.value) {
    try {
      await apiClient.post(`/rpa/session/${sessionId.value}/stop`);
    } catch (err) {
      console.error('Failed to stop session:', err);
    }
  }
  if (launchSource.value === 'mcp-tool-studio') {
    router.push(buildRpaToolEditorLocation({
      sessionId: sessionId.value || '',
    }));
    return;
  }
  router.push(`/rpa/configure?sessionId=${sessionId.value}`);
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const deleteStep = async (step: any, fallbackStepIndex: number) => {
  if (!sessionId.value) return;
  try {
    if (step.traceId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/trace/${step.traceId}`);
    } else if (step.diagnosticId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/diagnostic/${step.diagnosticId}`);
    } else if (step.stepId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/timeline-item`, {
        data: { kind: 'manual_step', step_id: step.stepId },
      });
    } else {
      await apiClient.delete(`/rpa/session/${sessionId.value}/step/${fallbackStepIndex}`);
    }
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    refreshTimeline(sessionWithTimeline(resp.data));
  } catch (err) {
    console.error('Failed to delete step:', err);
  }
};

const abortAgent = async () => {
  if (!sessionId.value) return;
  await apiClient.post(`/rpa/session/${sessionId.value}/agent/abort`);
};

const sendConfirm = async (approved: boolean) => {
  pendingConfirm.value = null;
  if (!sessionId.value) return;
  await apiClient.post(`/rpa/session/${sessionId.value}/agent/confirm`, { approved });
};

const handleComposerKeydown = (event: KeyboardEvent) => {
  if (!shouldSubmitRpaAssistantComposer(event)) return;
  event.preventDefault();
  sendMessage();
};

const sendMessage = async () => {
  if (!newMessage.value.trim() || !sessionId.value || sending.value) return;
  const userText = newMessage.value.trim();
  newMessage.value = '';
  sending.value = true;
  agentRunning.value = true;
  const initialProgress = getInitialRpaAgentProgress();

  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  chatMessages.value.push({ role: 'user', text: userText, time: now });

  const assistantMsg: ChatMessage = {
    role: 'assistant',
    text: '',
    time: now,
    status: initialProgress.status,
    processingLabel: initialProgress.label,
    run: createRpaAssistantRun(now),
  };
  chatMessages.value.push(assistantMsg);
  const msgIdx = chatMessages.value.length - 1;
  scrollAssistantToBottom();

  try {
    const resp = await fetch(`/api/v1/rpa/session/${sessionId.value}/chat`, {
      method: 'POST',
      headers: buildRpaAssistantRequestHeaders(),
      body: JSON.stringify(buildRpaAssistantChatPayload(userText, selectedModelId.value)),
    });

    if (!resp.ok || !resp.body) {
      chatMessages.value[msgIdx].text = '请求失败，请重试。';
      chatMessages.value[msgIdx].status = 'error';
      chatMessages.value[msgIdx].processingLabel = '';
      sending.value = false;
      agentRunning.value = false;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            const data = JSON.parse(raw);
            const progress = getRpaAgentProgressForEvent(eventType);
            if (progress) {
              chatMessages.value[msgIdx].status = progress.status;
              chatMessages.value[msgIdx].processingLabel = progress.label;
            }
            if (chatMessages.value[msgIdx].run) {
              chatMessages.value[msgIdx].run = applyRpaAssistantRunEvent(
                chatMessages.value[msgIdx].run!,
                eventType,
                data,
              );
            }
            if (eventType === 'message_chunk') {
              chatMessages.value[msgIdx].text += data.text || '';
            } else if (eventType === 'script') {
              chatMessages.value[msgIdx].script = data.code || '';
              chatMessages.value[msgIdx].text = cleanupAssistantText(
                chatMessages.value[msgIdx].text,
                data.code || '',
              );
            } else if (eventType === 'resolution') {
              const resolved = data.intent?.resolved || {};
              chatMessages.value[msgIdx].frameSummary = (resolved.frame_path || []).length
                ? (resolved.frame_path || []).join(' -> ')
                : 'Main frame';
              chatMessages.value[msgIdx].locatorSummary = resolved.selected_locator_kind || resolved.locator?.method || '';
              if (resolved.collection_hint?.kind) {
                chatMessages.value[msgIdx].collectionSummary = `${resolved.collection_hint.kind}${resolved.ordinal ? ` / ${resolved.ordinal}` : ''}`;
              }
            } else if (eventType === 'executing') {
              chatMessages.value[msgIdx].status = 'executing';
              chatMessages.value[msgIdx].processingLabel = '正在执行浏览器操作...';
              if (!chatMessages.value[msgIdx].text.trim()) {
                chatMessages.value[msgIdx].text = '代码已生成，正在执行浏览器操作。';
              }
            } else if (eventType === 'result') {
              chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
              chatMessages.value[msgIdx].processingLabel = '';
              if (data.error) chatMessages.value[msgIdx].error = data.error;
              if (data.output && data.output !== 'ok' && data.output !== 'None') {
                chatMessages.value[msgIdx].text += `${chatMessages.value[msgIdx].text ? '\n' : ''}输出: ${data.output}`;
              }
            } else if (eventType === 'agent_thought') {
              chatMessages.value[msgIdx].text += (chatMessages.value[msgIdx].text ? '\n' : '') + `💭 ${data.text || ''}`;
            } else if (eventType === 'agent_action') {
              const actionIdx = (chatMessages.value[msgIdx].actions?.length || 0);
              chatMessages.value[msgIdx].text += `\n⚡ ${data.description || ''} [[CODE_${actionIdx}]]`;
              if (!chatMessages.value[msgIdx].actions) chatMessages.value[msgIdx].actions = [];
              chatMessages.value[msgIdx].actions!.push({ description: data.description || '', code: data.code || '', showCode: false });
            } else if (eventType === 'agent_step_done') {
              if (data.trace) {
                acceptedTraces.value = [...acceptedTraces.value.filter((t: any) => t.trace_id !== data.trace.trace_id), data.trace];
                steps.value = mapServerTraces(acceptedTraces.value);
              }
              if (data.step) {
                const s = data.step;
                steps.value.push({
                  id: String(steps.value.length),
                  title: s.description || s.action,
                  description: s.prompt || s.description || 'AI 操作',
                  status: 'completed',
                  source: 'ai',
                  sensitive: s.sensitive || false,
                });
              }
              // Show output if present
              if (data.output) {
                const outputText = typeof data.output === 'string' ? data.output : JSON.stringify(data.output);
                chatMessages.value[msgIdx].text += `\nOutput: ${outputText}`;
              }
            } else if (eventType === 'trace_added') {
              if (data?.trace_id) {
                acceptedTraces.value = [...acceptedTraces.value.filter((t: any) => t.trace_id !== data.trace_id), data];
                steps.value = mapServerTraces(acceptedTraces.value);
              }
            } else if (eventType === 'confirm_required') {
              pendingConfirm.value = data;
            } else if (eventType === 'agent_done') {
              chatMessages.value[msgIdx].status = 'done';
              const completedCount = data.trace_count ?? data.total_steps ?? Math.max(steps.value.length - 1, 0);
              chatMessages.value[msgIdx].text += `\nTask completed, accepted ${completedCount} trace(s).`;
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'agent_aborted') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].text += `\n⚠️ Agent 已停止：${data.reason || ''}`;
              chatMessages.value[msgIdx].diagnostics = formatAgentDiagnostics(data.diagnostics || []);
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'error') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].error = data.message || '未知错误';
              agentRunning.value = false;
            }
            scrollAssistantToBottom();
          } catch { /* ignore parse errors */ }
          eventType = '';
        }
      }
    }

    if (!chatMessages.value[msgIdx].status || chatMessages.value[msgIdx].status === 'streaming') {
      chatMessages.value[msgIdx].status = 'done';
    }
  } catch (err: any) {
    chatMessages.value[msgIdx].text = `连接失败: ${err.message}`;
    chatMessages.value[msgIdx].status = 'error';
    chatMessages.value[msgIdx].processingLabel = '';
    agentRunning.value = false;
  } finally {
    sending.value = false;
  }
};
</script>

<template>
  <div class="flex flex-col h-screen bg-[#f5f6f7] dark:bg-[#161618] overflow-hidden">
    <!-- Header -->
    <RpaFlowGuide
      current-step="record"
      :session-id="sessionId"
      :recorded-step-count="Math.max(steps.length - 1, 0)"
      :diagnostic-count="recordingDiagnostics.length"
      :is-recording="isRecording"
      :recording-time="recordingTime"
      primary-label="完成录制"
      @home="goToHome"
      @skills="goToSkills"
      @go-configure="stopRecording"
      @primary-action="stopRecording"
    />

    <!-- Main Content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left Sidebar: Steps -->
      <aside class="flex w-80 flex-shrink-0 overflow-hidden bg-[#eff1f2] dark:bg-[#212122]">
        <RpaStepTimeline
          :steps="steps"
          mode="record"
          :is-recording="isRecording"
          :auto-scroll="true"
          :active-index="isRecording && steps.length ? steps.length - 1 : null"
          :diagnostics-count="recordingDiagnostics.length"
          diagnostics-message="这些步骤不会进入 accepted timeline，完成录制后需要在配置页修复或删除。"
          show-delete
          empty-message="在浏览器中操作后，步骤会自动出现在这里。"
          @delete-step="deleteStep($event.step, $event.index - 1)"
        />
      </aside>

      <!-- Center: Screencast Viewport -->
      <main class="flex-1 bg-[#f5f6f7] dark:bg-[#161618] px-5 py-4 flex flex-col min-w-0">
        <div class="flex-1 bg-[#1e1e1e] rounded-2xl shadow-2xl relative overflow-hidden flex flex-col border border-gray-800 dark:border-gray-600">
          <div class="h-9 bg-[#cfd3d8] dark:bg-[#2a2a2b] flex items-end px-3 gap-2 flex-shrink-0 overflow-x-auto">
            <button
              v-for="tab in tabs"
              :key="tab.tab_id"
              type="button"
              @click="activateTab(tab.tab_id)"
              class="max-w-[220px] min-w-[120px] h-7 px-3 rounded-t-xl text-[11px] border border-b-0 transition-colors truncate"
              :class="tab.active ? 'bg-[#f5f6f7] dark:bg-[#161618] text-gray-900 dark:text-gray-100 border-gray-300 dark:border-gray-600' : 'bg-white/60 dark:bg-white/10 text-gray-600 dark:text-gray-400 border-transparent hover:bg-white/80 dark:hover:bg-white/20'"
            >
              {{ tab.title || tab.url || 'New Tab' }}
            </button>
          </div>
          <div class="h-9 bg-[#dadddf] dark:bg-[#383839] flex items-center px-3 gap-2 flex-shrink-0 border-t border-white/40 dark:border-white/10">
            <div class="flex gap-1.5">
              <div class="w-2.5 h-2.5 rounded-full bg-red-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-yellow-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-green-400"></div>
            </div>
            <form class="flex-1 bg-white dark:bg-[#272728] rounded-md h-5 mx-3 flex items-center px-2 shadow-inner border border-transparent focus-within:border-[#831bd7]/30" @submit.prevent="submitAddressBar">
              <Globe class="text-gray-400 dark:text-gray-500 flex-shrink-0" :size="12" />
              <input
                v-model="addressInput"
                class="flex-1 bg-transparent text-[10px] text-gray-700 dark:text-gray-300 ml-2 outline-none placeholder:text-gray-400"
                :disabled="!sessionId || isNavigating"
                placeholder="输入网址并按回车跳转"
                type="text"
                spellcheck="false"
                @focus="isAddressEditing = true"
                @blur="handleAddressBlur"
              />
              <span v-if="isNavigating" class="text-[9px] text-[#831bd7] font-medium flex-shrink-0">打开中...</span>
            </form>
          </div>

          <div class="flex-1 relative bg-black overflow-hidden">
            <canvas
              v-if="sessionId"
              ref="canvasRef"
              class="w-full h-full object-contain cursor-default"
              tabindex="0"
              @click="focusCanvas"
              @mousedown="sendInputEvent"
              @mouseup="sendInputEvent"
              @mousemove="sendInputEvent"
              @wheel.prevent="sendInputEvent"
              @keydown.prevent="sendInputEvent"
              @keyup.prevent="sendInputEvent"
              @contextmenu.prevent
            />
            <div v-else class="absolute inset-0 flex items-center justify-center flex-col gap-4 text-white/50">
              <Terminal :size="64" class="opacity-20" />
              <p v-if="loading" class="text-sm font-medium">正在启动 Playwright 浏览器...</p>
              <p v-else-if="error" class="text-sm font-medium text-red-400">{{ error }}</p>
            </div>

            <div v-if="error && sessionId" class="absolute top-4 right-4 max-w-xs bg-red-500/90 text-white text-[11px] px-3 py-2 rounded-lg shadow-lg">
              {{ error }}
            </div>
            <div v-if="sessionId" class="absolute bottom-3 left-1/2 -translate-x-1/2 bg-white/10 backdrop-blur-md border border-white/20 px-3 py-1.5 rounded-full flex items-center gap-2">
              <Radio class="text-red-400 animate-pulse" :size="14" />
              <span class="text-white text-[10px] font-bold tracking-wider uppercase">实时 CDP 串流</span>
            </div>
          </div>
        </div>

        <div v-if="false" class="mt-6 flex justify-center gap-4">
          <button class="flex items-center gap-2 bg-white dark:bg-[#272728] px-5 py-2.5 rounded-xl text-xs font-bold text-gray-700 dark:text-gray-300 shadow-sm hover:bg-gray-50 dark:hover:bg-[#444345] transition-colors border border-gray-200 dark:border-gray-700">
            <Camera :size="16" class="text-gray-500 dark:text-gray-400" />
            截图标记
          </button>
        </div>
      </main>

      <!-- Right Sidebar: AI Chat -->
      <aside class="w-80 bg-[#eff1f2] dark:bg-[#212122] border-l border-gray-200 dark:border-gray-700 flex flex-col shadow-[-10px_0_40px_-10px_rgba(0,0,0,0.03)]">
        <div class="p-6 border-b border-gray-100 dark:border-gray-800 bg-[#eff1f2] dark:bg-[#212122]">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-[#831bd7] to-[#ac0089] flex items-center justify-center text-white shadow-lg shadow-purple-200">
                <Wand2 :size="20" />
              </div>
              <div>
                <h3 class="text-gray-900 dark:text-gray-100 font-bold text-sm">AI 录制助手</h3>
                <p
                  class="text-[10px] font-bold"
                  :class="agentRunning ? 'text-orange-500' : 'text-[#831bd7]'"
                  v-text="agentRunning ? '正在处理你的操作...' : '按描述录制操作'"
                ></p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button
                v-if="agentRunning"
                @click="abortAgent"
                class="text-[10px] font-bold text-red-500 border border-red-200 dark:border-red-800 px-2 py-1 rounded-lg hover:bg-red-50 transition-colors"
              >中止</button>
            </div>
          </div>
        </div>

        <div ref="chatScrollRef" class="flex-1 overflow-y-auto p-6 space-y-6 bg-[#eff1f2] dark:bg-[#212122]">
          <div v-if="chatMessages.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-xs mt-8">
            在 VNC 中操作浏览器，步骤会自动记录到左侧面板。
          </div>
          <div
            v-for="(msg, idx) in chatMessages"
            :key="idx"
            class="flex flex-col gap-1.5"
            :class="msg.role === 'user' ? 'items-end' : 'items-start'"
          >
            <div
              v-if="msg.role === 'user'"
              class="max-w-[85%] min-w-0 overflow-hidden rounded-2xl rounded-tr-none bg-[#831bd7] p-3 text-xs leading-relaxed text-white shadow-md shadow-purple-100 break-words [overflow-wrap:anywhere]"
            >
              <div class="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{{ msg.text }}</div>
            </div>

            <div
              v-else-if="msg.run"
              class="w-full max-w-[94%] overflow-hidden rounded-lg bg-white p-3 text-xs text-gray-800 shadow-[0_16px_36px_rgba(25,28,30,0.06)] dark:bg-[#272728] dark:text-gray-200"
            >
              <div class="flex min-w-0 items-start justify-between gap-2">
                <div class="flex min-w-0 items-center gap-2">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[#831bd7] to-[#ac0089] text-white">
                    <Wand2 :size="16" />
                  </div>
                  <div class="min-w-0">
                    <div class="truncate text-[12px] font-bold text-gray-950 dark:text-gray-100">任务处理进度</div>
                    <div class="mt-0.5 flex flex-wrap gap-1 text-[9px] font-semibold text-gray-500 dark:text-gray-400">
                      <span>{{ runRoundCount(msg.run) }} 次尝试</span>
                      <span>·</span>
                      <span>已记录 {{ getRunTraceCount(msg) }} 步</span>
                      <span>·</span>
                      <span>{{ msg.time }}</span>
                    </div>
                  </div>
                </div>
                <span class="shrink-0 rounded px-2 py-1 text-[9px] font-bold" :class="getRunStatusClass(msg)">
                  {{ getRunStatusLabel(msg) }}
                </span>
              </div>

              <div v-if="msg.run.rounds.length === 0" class="mt-3 rounded-lg bg-[#f2f4f6] p-3 dark:bg-white/[0.08]">
                <div class="flex items-center gap-2 text-[11px] font-semibold text-[#831bd7] dark:text-purple-200">
                  <Loader2 :size="13" class="animate-spin" />
                  <span>{{ msg.processingLabel || 'Agent 正在规划录制步骤...' }}</span>
                </div>
              </div>

              <div v-else class="mt-3 space-y-2">
                <section
                  v-for="round in msg.run.rounds"
                  :key="round.id"
                  class="rounded-lg bg-[#f8f9fb] p-2.5 dark:bg-white/[0.06]"
                >
                  <div class="mb-2 flex items-center justify-between gap-2">
                    <div class="text-[10px] font-bold text-gray-900 dark:text-gray-100">第 {{ round.index }} 次尝试</div>
                    <span class="rounded px-1.5 py-0.5 text-[9px] font-bold" :class="getRoundStatusClass(round)">
                      {{ getRoundStatusLabel(round) }}
                    </span>
                  </div>

                  <div class="space-y-1.5">
                    <div
                      v-for="item in round.items"
                      :key="item.id"
                      class="min-w-0 rounded-md p-2"
                      :class="getRunItemToneClass(item)"
                    >
                      <div class="flex min-w-0 gap-2">
                        <div class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-white/70 dark:bg-black/20">
                          <Bot v-if="item.kind === 'plan'" :size="12" />
                          <Terminal v-else-if="item.kind === 'action'" :size="12" />
                          <ClipboardCheck v-else-if="item.kind === 'trace'" :size="12" />
                          <AlertCircle v-else-if="item.kind === 'diagnostic'" :size="12" />
                          <CheckCircle v-else :size="12" />
                        </div>
                        <div class="min-w-0 flex-1">
                          <div class="break-words text-[11px] font-bold leading-snug [overflow-wrap:anywhere]">{{ item.title }}</div>
                          <div v-if="item.detail" class="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed opacity-85 [overflow-wrap:anywhere]">{{ item.detail }}</div>
                          <button
                            v-if="item.code"
                            @click="item.showCode = !item.showCode"
                            class="mt-2 inline-flex items-center gap-1 rounded bg-white/70 px-2 py-1 text-[10px] font-bold text-[#831bd7] transition hover:bg-white dark:bg-black/20 dark:text-purple-200"
                          >
                            <Code :size="11" />
                            {{ item.showCode ? '收起技术细节' : '查看技术细节' }}
                            <ChevronUp v-if="item.showCode" :size="11" />
                            <ChevronDown v-else :size="11" />
                          </button>
                          <pre v-if="item.code && item.showCode" class="mt-2 max-h-40 overflow-auto rounded-md bg-[#101828] p-2 text-[10px] leading-relaxed text-emerald-200"><code>{{ item.code }}</code></pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </section>
              </div>

              <div
                v-if="msg.status === 'done'"
                class="mt-3 flex items-start gap-2 rounded-lg bg-emerald-50 p-2 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
              >
                <CheckCircle :size="13" class="mt-0.5 shrink-0" />
                <span class="min-w-0 break-words">任务完成，已记录 {{ getRunTraceCount(msg) }} 个可回放步骤。</span>
              </div>

              <div
                v-if="msg.status === 'error' && (msg.run.error || msg.run.diagnostics.length)"
                class="mt-3 rounded-lg bg-red-50 p-2 text-[10px] text-red-700 dark:bg-red-950/30 dark:text-red-200"
              >
                <div class="mb-1 flex items-center gap-1 font-bold">
                  <AlertCircle :size="12" />
                  <span>失败诊断</span>
                </div>
                <div v-if="msg.run.error" class="whitespace-pre-wrap break-words font-mono [overflow-wrap:anywhere]">{{ msg.run.error }}</div>
                <div v-for="(diagnostic, didx) in msg.run.diagnostics" :key="didx" class="mt-1 whitespace-pre-wrap break-words font-mono opacity-90 [overflow-wrap:anywhere]">
                  {{ didx + 1 }}. {{ diagnostic }}
                </div>
              </div>
            </div>

            <div
              v-else
              class="max-w-[85%] min-w-0 overflow-hidden rounded-2xl rounded-tl-none bg-white p-3 text-xs leading-relaxed text-gray-700 shadow-sm dark:bg-[#272728] dark:text-gray-300"
            >
              <div class="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{{ msg.text }}</div>
            </div>
            <span class="text-[9px] text-gray-400 dark:text-gray-500 font-medium px-1">{{ msg.time }}</span>
          </div>

          <!-- Inline confirm dialog for high-risk operations -->
          <div v-if="pendingConfirm" class="bg-orange-50 dark:bg-orange-900/30 border border-orange-200 dark:border-orange-800 rounded-xl p-4 text-xs">
            <p class="font-bold text-orange-700 mb-1">⚠️ 高危操作确认</p>
            <p class="text-gray-700 dark:text-gray-300 mb-1">{{ pendingConfirm.description }}</p>
            <p class="text-orange-600 text-[10px] mb-3">风险：{{ pendingConfirm.risk_reason }}</p>
            <div class="flex gap-2">
              <button @click="sendConfirm(true)" class="flex-1 bg-orange-500 text-white rounded-lg py-1.5 font-bold hover:bg-orange-600 transition-colors">确认执行</button>
              <button @click="sendConfirm(false)" class="flex-1 bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 rounded-lg py-1.5 font-bold hover:bg-gray-50 dark:hover:bg-[#444345] transition-colors">跳过</button>
            </div>
          </div>
        </div>

        <div class="bg-[#eff1f2] p-4 dark:bg-[#212122]">
          <div class="relative rounded-2xl bg-white p-2 shadow-[0_16px_36px_rgba(25,28,30,0.08)] ring-1 ring-black/[0.04] dark:bg-[#272728] dark:ring-white/10">
            <textarea
              v-model="newMessage"
              @keydown="handleComposerKeydown"
              :disabled="sending || agentRunning"
              class="h-[56px] min-h-[56px] max-h-[56px] w-full resize-none overflow-y-auto whitespace-pre-wrap break-words bg-transparent px-2 pb-2 pt-1 text-xs leading-relaxed text-gray-800 outline-none placeholder:text-gray-400 disabled:opacity-50 dark:text-gray-100"
              :placeholder="agentRunning ? 'Agent 运行中...' : (sending ? 'AI 正在处理...' : '描述录制目标或操作...')"
              rows="2"
            ></textarea>
            <div class="flex items-center justify-between gap-2">
              <div class="relative min-w-0 flex-1">
                <button
                  type="button"
                  class="flex h-7 max-w-full items-center gap-1.5 rounded-lg bg-[#f2f4f6] px-2 text-left text-[10px] font-semibold text-gray-700 transition-colors hover:bg-[#edeef0] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/10 dark:text-gray-200 dark:hover:bg-white/[0.14]"
                  :disabled="models.length === 0 || sending || agentRunning"
                  @click="modelDropdownOpen = !modelDropdownOpen"
                >
                  <ProviderIcon v-if="selectedModel" :provider="selectedModel.provider" class="size-3.5 flex-shrink-0" />
                  <span class="min-w-0 truncate">{{ selectedModelName }}</span>
                  <span v-if="selectedModel" class="hidden max-w-12 truncate text-[9px] text-gray-400 sm:inline">{{ selectedModel.provider }}</span>
                  <ChevronDown :size="12" class="flex-shrink-0 text-gray-400 transition-transform" :class="modelDropdownOpen && 'rotate-180'" />
                </button>
                <div
                  v-if="modelDropdownOpen"
                  class="absolute bottom-full left-0 z-30 mb-2 w-64 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl bg-white/95 p-1 shadow-[0_24px_48px_rgba(25,28,30,0.14)] ring-1 ring-black/[0.06] backdrop-blur dark:bg-[#272728]/95 dark:ring-white/10"
                >
                  <button
                    v-for="model in models"
                    :key="model.id"
                    type="button"
                    class="flex w-full min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left text-xs transition-colors hover:bg-gray-50 dark:hover:bg-white/10"
                    :class="selectedModelId === model.id ? 'bg-purple-50 text-[#831bd7] dark:bg-[#831bd7]/20 dark:text-purple-200' : 'text-gray-700 dark:text-gray-200'"
                    @click="selectAssistantModel(model.id)"
                  >
                    <ProviderIcon :provider="model.provider" class="size-4 flex-shrink-0" />
                    <span class="min-w-0 flex-1 truncate">{{ modelDisplayName(model) }}</span>
                    <span class="max-w-16 truncate text-[10px] text-gray-400">{{ model.provider }}</span>
                    <CheckCircle v-if="selectedModelId === model.id" :size="13" class="flex-shrink-0" />
                  </button>
                </div>
              </div>
              <button
                @click="sendMessage"
                :disabled="sending || agentRunning"
                class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[#831bd7] to-[#ac0089] text-white shadow-[0_10px_22px_rgba(131,27,215,0.24)] transition-transform hover:scale-105 disabled:scale-100 disabled:opacity-50"
              >
                <Send :size="15" />
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

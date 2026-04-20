<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Camera, Terminal, CheckCircle, Radio, Send, Wand2, Bot, Code, X, House, FolderOpen, Globe } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { completeRecordingSegment } from '@/api/recording';
import { deriveArtifactsFromRpaSteps, mapRpaStepsToRecordingSteps, type RpaStep } from '@/utils/recording';
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

const router = useRouter();
const route = useRoute();

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

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  time: string;
  script?: string;
  status?: 'streaming' | 'executing' | 'done' | 'error';
  error?: string;
  showCode?: boolean;
  actions?: Array<{ description: string; code: string; showCode?: boolean }>;  // Track agent actions
  frameSummary?: string;
  locatorSummary?: string;
  collectionSummary?: string;
  diagnostics?: string[];
}

const chatMessages = ref<ChatMessage[]>([]);
const newMessage = ref('');
const sending = ref(false);
const agentMode = ref(false);
const agentRunning = ref(false);

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

const codeActionIndex = (part: string) => Number(part.match(/\[\[CODE_(\d+)\]\]/)?.[1] ?? -1);

const cleanupAssistantText = (text: string, script = '') => {
  let next = text;
  next = next.replace(/^正在分析当前页面\.\.\.\s*/u, '');
  next = next.replace(/```python[\s\S]*?```/gu, '');
  if (script) {
    next = next.replace(script, '');
  }
  return next.trim();
};

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
      const serverSteps = resp.data.session?.steps || [];
      if (serverSteps.length > 0) {
        steps.value = mapServerSteps(serverSteps);
      }
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

  const chatSessionId = route.query.chatSessionId as string | undefined;
  const runId = route.query.runId as string | undefined;
  const segmentId = route.query.segmentId as string | undefined;
  const returnTo = (route.query.returnTo as string | undefined) || (chatSessionId ? `/chat/${chatSessionId}` : '/chat');

  if (sessionId.value && chatSessionId && runId && segmentId) {
    try {
      const response = await apiClient.get(`/rpa/session/${sessionId.value}`);
      const rawSteps: RpaStep[] = response.data.session?.steps || [];
      await completeRecordingSegment(
        chatSessionId,
        runId,
        segmentId,
        {
          rpa_session_id: sessionId.value,
          steps: mapRpaStepsToRecordingSteps(rawSteps),
          artifacts: deriveArtifactsFromRpaSteps(rawSteps),
        },
      );
    } catch (err: any) {
      console.error('Failed to complete conversational recording segment:', err);
      error.value = `无法完成对话录制段：${err.response?.data?.detail || err.message || '未知错误'}`;
      isRecording.value = true;
      startTimer();
      startPollingSteps();
      return;
    }
  }

  if (sessionId.value) {
    try {
      await apiClient.post(`/rpa/session/${sessionId.value}/stop`);
    } catch (err) {
      console.error('Failed to stop session:', err);
    }
  }
  if (chatSessionId && runId && segmentId) {
    router.push(returnTo);
  } else {
    router.push(`/rpa/configure?sessionId=${sessionId.value}`);
  }
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const deleteStep = async (stepIndex: number) => {
  if (!sessionId.value) return;
  try {
    await apiClient.delete(`/rpa/session/${sessionId.value}/step/${stepIndex}`);
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const serverSteps = resp.data.session?.steps || [];
    steps.value = mapServerSteps(serverSteps);
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

const sendMessage = async () => {
  if (!newMessage.value.trim() || !sessionId.value || sending.value) return;
  const userText = newMessage.value.trim();
  newMessage.value = '';
  sending.value = true;
  if (agentMode.value) agentRunning.value = true;

  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  chatMessages.value.push({ role: 'user', text: userText, time: now });

  const assistantMsg: ChatMessage = { role: 'assistant', text: '', time: now, status: 'streaming' };
  chatMessages.value.push(assistantMsg);
  const msgIdx = chatMessages.value.length - 1;

  try {
    const resp = await fetch(`/api/v1/rpa/session/${sessionId.value}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({ message: userText, mode: agentMode.value ? 'react' : 'chat' }),
    });

    if (!resp.ok || !resp.body) {
      chatMessages.value[msgIdx].text = '请求失败，请重试。';
      chatMessages.value[msgIdx].status = 'error';
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
              if (!chatMessages.value[msgIdx].text.trim()) {
                chatMessages.value[msgIdx].text = '代码已生成，正在执行浏览器操作。';
              }
            } else if (eventType === 'result') {
              chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
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
                chatMessages.value[msgIdx].text += `\n✓ 输出：${data.output}`;
              }
            } else if (eventType === 'confirm_required') {
              pendingConfirm.value = data;
            } else if (eventType === 'agent_done') {
              chatMessages.value[msgIdx].status = 'done';
              chatMessages.value[msgIdx].text += `\n✅ 任务完成，共执行 ${data.total_steps ?? 0} 步`;
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'agent_aborted') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].text += `\n⚠️ Agent 已停止：${data.reason || ''}`;
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'error') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].error = data.message || '未知错误';
              agentRunning.value = false;
            }
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
    agentRunning.value = false;
  } finally {
    sending.value = false;
  }
};
</script>

<template>
  <div class="flex flex-col h-screen bg-[#f5f6f7] dark:bg-[#161618] overflow-hidden">
    <!-- Header -->
    <header class="h-16 flex-shrink-0 bg-gradient-to-r from-[#831bd7] to-[#ac0089] shadow-lg flex justify-between items-center px-8 z-50">
      <div class="flex items-center gap-4">
        <Radio class="text-white animate-pulse" :size="24" />
        <h1 class="text-white font-extrabold text-xl tracking-tight">技能录制器</h1>
        <div class="ml-4 px-3 py-1 bg-white/20 rounded-full flex items-center gap-2">
          <div class="w-2 h-2 rounded-full bg-red-400 animate-pulse"></div>
          <span class="text-white/90 text-[10px] font-bold uppercase tracking-wider">正在录制 ({{ recordingTime }})</span>
        </div>
      </div>
      <div class="flex items-center gap-4">
        <button
          @click="goToHome"
          class="flex items-center gap-2 bg-white/10 text-white font-medium px-4 py-2 rounded-full hover:bg-white/20 transition-all text-sm"
        >
          <House :size="16" />
          返回首页
        </button>
        <button
          @click="goToSkills"
          class="flex items-center gap-2 bg-white/10 text-white font-medium px-4 py-2 rounded-full hover:bg-white/20 transition-all text-sm"
        >
          <FolderOpen :size="16" />
          技能库
        </button>
        <button
          @click="stopRecording"
          class="bg-white dark:bg-[#272728] text-[#831bd7] font-bold px-6 py-2 rounded-full hover:bg-white/90 transition-all shadow-md active:scale-95 text-sm"
        >
          完成录制
        </button>
      </div>
    </header>

    <!-- Main Content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left Sidebar: Steps -->
      <aside class="w-80 bg-[#eff1f2] dark:bg-[#212122] border-r border-gray-200 dark:border-gray-700 p-6 overflow-y-auto flex flex-col">
        <div class="flex items-center justify-between mb-8">
          <h2 class="text-gray-900 dark:text-gray-100 font-extrabold text-lg">录制步骤</h2>
          <span class="text-[#831bd7] text-[10px] font-bold bg-[#c384ff]/20 px-2 py-1 rounded-md">{{ steps.length }} 步</span>
        </div>

        <div class="space-y-4">
          <div
            v-for="(step, index) in steps"
            :key="step.id"
            class="bg-white dark:bg-[#272728] p-4 rounded-xl shadow-sm border-l-4 transition-all group relative"
            :class="[ step.source === 'ai' ? 'border-[#ac0089]' : (step.status === 'active' ? 'border-[#831bd7]' : 'border-gray-200 dark:border-gray-700 opacity-70') ]"
          >
            <div class="flex justify-between items-start mb-1">
              <div class="flex items-center gap-1.5">
                <Bot v-if="step.source === 'ai'" class="text-[#ac0089]" :size="12" />
                <p class="text-[10px] font-bold" :class="step.source === 'ai' ? 'text-[#ac0089]' : (step.status === 'active' ? 'text-[#831bd7]' : 'text-gray-400 dark:text-gray-500')">
                  {{ step.source === 'ai' ? 'AI' : '步骤' }} {{ step.id.padStart(2, '0') }}
                </p>
              </div>
              <div class="flex items-center gap-1">
                <button
                  v-if="index > 0"
                  @click="deleteStep(index - 1)"
                  class="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-red-50 rounded"
                  title="删除步骤"
                >
                  <X class="text-red-500" :size="14" />
                </button>
                <CheckCircle v-if="step.status === 'completed'" class="text-green-500" :size="14" />
              </div>
            </div>
            <h3 class="text-gray-900 dark:text-gray-100 font-semibold text-sm">{{ step.title }}</h3>
            <p class="text-gray-500 dark:text-gray-400 text-[11px] mt-2 leading-relaxed">{{ step.description }}</p>
            <div v-if="step.locatorSummary || step.frameSummary || step.validationStatus" class="mt-3 space-y-1.5 text-[10px] text-gray-500 dark:text-gray-400">
              <p v-if="step.locatorSummary" class="break-all">
                <span class="font-semibold text-gray-600 dark:text-gray-400">Locator:</span>
                <span class="font-mono ml-1">{{ step.locatorSummary }}</span>
              </p>
              <p v-if="step.frameSummary" class="break-all">
                <span class="font-semibold text-gray-600 dark:text-gray-400">Frame:</span>
                <span class="font-mono ml-1">{{ step.frameSummary }}</span>
              </p>
              <p v-if="step.validationStatus" class="break-all">
                <span class="font-semibold text-gray-600 dark:text-gray-400">Validation:</span>
                <span
                  class="ml-1 px-1.5 py-0.5 rounded-full"
                  :class="getValidationClass(step.validationStatus)"
                >
                  {{ getValidationLabel(step.validationStatus) }}
                </span>
                <span v-if="step.validationDetails" class="ml-1">{{ step.validationDetails }}</span>
              </p>
            </div>
          </div>

          <div v-if="isRecording" class="flex flex-col items-center justify-center py-8 gap-3 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-xl opacity-60">
            <div class="animate-spin text-[#831bd7]">
              <Wand2 :size="20" />
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 font-medium">检测新操作中...</p>
          </div>
        </div>
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
                <p class="text-[10px] font-bold" :class="agentRunning ? 'text-orange-500' : 'text-[#831bd7]'">
                  {{ agentRunning ? 'Agent 运行中...' : (agentMode ? 'Agent 模式' : '已就绪 · 协助录制中') }}
                </p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button
                v-if="agentRunning"
                @click="abortAgent"
                class="text-[10px] font-bold text-red-500 border border-red-200 dark:border-red-800 px-2 py-1 rounded-lg hover:bg-red-50 transition-colors"
              >中止</button>
              <label class="flex items-center gap-1.5 cursor-pointer" :class="agentRunning ? 'opacity-50 pointer-events-none' : ''">
                <span class="text-[10px] text-gray-500 dark:text-gray-400 font-medium">Agent</span>
                <div
                  @click="agentMode = !agentMode"
                  class="w-8 h-4 rounded-full transition-colors relative"
                  :class="agentMode ? 'bg-[#831bd7]' : 'bg-gray-300'"
                >
                  <div class="w-3 h-3 bg-white dark:bg-[#272728] rounded-full absolute top-0.5 transition-transform shadow-sm"
                    :class="agentMode ? 'translate-x-4' : 'translate-x-0.5'"
                  ></div>
                </div>
              </label>
            </div>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-6 space-y-6 bg-[#eff1f2] dark:bg-[#212122]">
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
              class="max-w-[85%] p-3 rounded-2xl text-xs leading-relaxed"
              :class="msg.role === 'user' ? 'bg-[#831bd7] text-white rounded-tr-none shadow-md shadow-purple-100' : 'bg-white dark:bg-[#272728] text-gray-700 dark:text-gray-300 rounded-tl-none border border-gray-100 dark:border-gray-800'"
            >
              <!-- Message text with inline code blocks for agent actions -->
              <div v-if="msg.actions && msg.actions.length > 0">
                <template v-for="(part, pidx) in msg.text.split(/(\[\[CODE_\d+\]\])/)" :key="pidx">
                  <span v-if="!part.match(/\[\[CODE_(\d+)\]\]/)">{{ part }}</span>
                  <div v-else class="inline-block ml-2">
                    <button
                      @click="msg.actions[codeActionIndex(part)].showCode = !msg.actions[codeActionIndex(part)].showCode"
                      class="inline-flex items-center gap-1 text-[10px] text-[#831bd7] hover:underline font-medium"
                    >
                      <Code :size="10" />
                      {{ msg.actions[codeActionIndex(part)].showCode ? '收起' : '查看代码' }}
                    </button>
                    <pre v-if="msg.actions[codeActionIndex(part)].showCode" class="mt-1 bg-gray-900 dark:bg-gray-800 text-green-300 text-[10px] p-2 rounded-lg overflow-x-auto max-h-32 overflow-y-auto"><code>{{ msg.actions[codeActionIndex(part)].code }}</code></pre>
                  </div>
                </template>
              </div>
              <div v-else class="whitespace-pre-wrap">{{ msg.text }}</div>
              <div v-if="msg.status === 'executing'" class="mt-2 flex items-center gap-1.5 text-[10px] text-[#831bd7] font-medium">
                <div class="w-2 h-2 rounded-full bg-[#831bd7] animate-pulse"></div>
                正在执行...
              </div>
              <div v-if="msg.status === 'error' && msg.error" class="mt-2 text-[10px] text-red-500 bg-red-50 dark:bg-red-900/30 p-2 rounded-lg">
                {{ msg.error }}
              </div>
              <div v-if="msg.frameSummary || msg.collectionSummary || msg.locatorSummary" class="mt-2 space-y-1 text-[10px] text-gray-500 dark:text-gray-400">
                <div v-if="msg.frameSummary">
                  <span class="font-semibold text-gray-600 dark:text-gray-400">Frame:</span>
                  <span class="ml-1 font-mono">{{ msg.frameSummary }}</span>
                </div>
                <div v-if="msg.collectionSummary">
                  <span class="font-semibold text-gray-600 dark:text-gray-400">Collection:</span>
                  <span class="ml-1">{{ msg.collectionSummary }}</span>
                </div>
                <div v-if="msg.locatorSummary">
                  <span class="font-semibold text-gray-600 dark:text-gray-400">Locator:</span>
                  <span class="ml-1">{{ msg.locatorSummary }}</span>
                </div>
              </div>
              <div v-if="msg.status === 'done' && msg.role === 'assistant' && !agentMode" class="mt-2 flex items-center gap-1 text-[10px] text-green-600 font-medium">
                <CheckCircle :size="10" /> 执行成功
              </div>
              <!-- Legacy script toggle (for non-agent mode) -->
              <button
                v-if="msg.script"
                @click="msg.showCode = !msg.showCode"
                class="mt-2 flex items-center gap-1 text-[10px] text-[#831bd7] hover:underline font-medium"
              >
                <Code :size="10" />
                {{ msg.showCode ? '收起代码' : '查看代码' }}
              </button>
              <pre v-if="msg.script && msg.showCode" class="mt-2 bg-gray-900 dark:bg-gray-800 text-green-300 text-[10px] p-3 rounded-lg overflow-x-auto max-h-48 overflow-y-auto"><code>{{ msg.script }}</code></pre>
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

        <div class="p-4 bg-[#eff1f2] dark:bg-[#212122] border-t border-gray-100 dark:border-gray-800">
          <div class="relative">
            <input
              v-model="newMessage"
              @keyup.enter="sendMessage"
              :disabled="sending || agentRunning"
              class="w-full bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 rounded-2xl py-3 pl-4 pr-12 text-xs focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm placeholder:text-gray-400 outline-none disabled:opacity-50"
              :placeholder="agentRunning ? 'Agent 运行中...' : (sending ? 'AI 正在处理...' : (agentMode ? '描述目标任务...' : '向助手提问...'))"
              type="text"
            />
            <button
              @click="sendMessage"
              :disabled="sending || agentRunning"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-[#831bd7] hover:scale-110 transition-transform p-1.5 disabled:opacity-50"
            >
              <Send :size="16" />
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

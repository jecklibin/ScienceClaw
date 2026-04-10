<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Play, Save, CheckCircle, XCircle, Loader2, Terminal, Code, ArrowLeft, RotateCcw, House, FolderOpen, Globe } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { getBackendWsUrl } from '@/utils/sandbox';
import { createReconnectableScreencast, type ScreencastStatusEvent } from '@/utils/reconnectableScreencast';

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
let screencastConnection: ReturnType<typeof createReconnectableScreencast> | null = null;
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
const saving = ref(false);
const saved = ref(false);
const showScript = ref(false);
const error = ref<string | null>(null);

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
  if (locator.method === 'css') return locator.value || 'css';
  return `${locator.method || 'locator'}:${locator.value || locator.name || ''}`;
};

const formatFramePath = (framePath?: string[]) => {
  if (!framePath?.length) return 'Main frame';
  return framePath.join(' -> ');
};

const loadSessionDiagnostics = async () => {
  if (!sessionId.value) return;
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    recordedSteps.value = resp.data.session?.steps || [];
  } catch (err) {
    console.error('[TestPage] Failed to load session diagnostics:', err);
  }
};

const drawFrame = (base64Data: string, _metadata: { width: number; height: number }) => {
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

const connectScreencast = (sid: string) => {
  screencastConnection?.stop();
  const updateScreencastStatus = (event: ScreencastStatusEvent) => {
    if (event.phase === 'open') {
      error.value = null;
      return;
    }
    if (event.phase === 'reconnecting') {
      if ((event.attempt ?? 0) >= 2) {
        error.value = `测试画面流连接不稳定，正在重连（第 ${event.attempt} 次）...`;
      }
      return;
    }
    if (event.phase === 'failed') {
      const code = event.close?.code ?? 1006;
      const reason = event.close?.reason ? `, reason=${event.close.reason}` : '';
      error.value = `测试画面流重连失败（code=${code}${reason}）`;
    }
  };

  screencastConnection = createReconnectableScreencast({
    debugLabel: 'TestPage',
    getUrl: () => getBackendWsUrl(`/rpa/screencast/${sid}`),
    onStatusChange: updateScreencastStatus,
    onError: () => {},
    onMessage: (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        console.log('[TestPage] Screencast message:', msg.type);
        if (msg.type === 'frame') {
          error.value = null;
          drawFrame(msg.data, msg.metadata);
        } else if (msg.type === 'tabs_snapshot') {
          tabs.value = msg.tabs || [];
          const active = tabs.value.find((tab) => tab.active);
          activeTabId.value = active?.tab_id || null;
        } else if (msg.type === 'preview_error') {
          error.value = msg.message || 'Preview switch failed';
        } else if (msg.type === 'ping') {
          return;
        }
      } catch (e) {
        console.error('[TestPage] Parse error:', e);
      }
    },
  });
  screencastConnection.connect();
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
  testing.value = true;
  testDone.value = false;
  testLogs.value = ['正在生成并执行 Playwright 脚本...'];

  try {
    // Start test execution (non-blocking)
    const testPromise = apiClient.post(`/rpa/session/${sessionId.value}/test`, {
      params: params.value,
    }, {
      timeout: TEST_REQUEST_TIMEOUT_MS,
    });

    // Connect screencast after a short delay to let backend create page
    await new Promise(resolve => setTimeout(resolve, 500));
    connectScreencast(sessionId.value);

    const resp = await testPromise;

    const result = resp.data.result || {};
    testOutput.value = result.output || '';
    testLogs.value = resp.data.logs || [];
    generatedScript.value = resp.data.script || '';
    testSuccess.value = result.success !== false;
    testDone.value = true;
  } catch (err: any) {
    testLogs.value.push(`错误: ${err.response?.data?.detail || err.message}`);
    testSuccess.value = false;
    testDone.value = true;
  } finally {
    testing.value = false;
  }
};

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
    error.value = '保存失败: ' + (err.response?.data?.detail || err.message);
  } finally {
    saving.value = false;
  }
};

onMounted(() => {
  loadSessionDiagnostics();
  runTest();
});

onBeforeUnmount(() => {
  screencastConnection?.stop();
  screencastConnection = null;
});
</script>

<template>
  <div class="h-screen flex flex-col bg-[#f5f6f7] overflow-hidden">
    <!-- Header -->
    <header class="h-14 bg-white border-b border-gray-200 flex items-center px-6 gap-3 flex-shrink-0">
      <button
        @click="goBackToConfigure"
        class="flex items-center gap-1 text-gray-500 hover:text-gray-700 transition-colors"
      >
        <ArrowLeft :size="18" />
      </button>
      <Play class="text-[#831bd7]" :size="22" />
      <h1 class="text-gray-900 font-extrabold text-lg">测试技能</h1>
      <span class="text-sm text-gray-500 truncate max-w-48">{{ skillName }}</span>
      <div class="flex-1"></div>

      <button
        @click="goToHome"
        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:text-gray-900 hover:bg-gray-50 transition-colors text-sm"
      >
        <House :size="15" />
        返回首页
      </button>
      <button
        @click="goToSkills"
        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:text-gray-900 hover:bg-gray-50 transition-colors text-sm"
      >
        <FolderOpen :size="15" />
        技能库
      </button>

      <div v-if="saved" class="flex items-center gap-2 text-green-600 font-bold text-sm">
        <CheckCircle :size="18" />
        技能已保存，正在跳转...
      </div>
    </header>

    <!-- Main content: VNC left + sidebar right -->
    <div class="flex-1 flex min-h-0">
      <!-- Left: VNC viewport (fills available space like RecorderPage) -->
      <main class="flex-1 p-6 flex flex-col min-w-0">
        <div class="flex-1 bg-[#1e1e1e] rounded-2xl shadow-2xl relative overflow-hidden flex flex-col border border-gray-800">
          <div class="h-11 bg-[#cfd3d8] flex items-end px-3 gap-2 flex-shrink-0 overflow-x-auto">
            <button
              v-for="tab in tabs"
              :key="tab.tab_id"
              type="button"
              @click="activateTab(tab.tab_id)"
              class="max-w-[220px] min-w-[120px] h-8 px-3 rounded-t-xl text-[11px] border border-b-0 transition-colors truncate"
              :class="tab.active ? 'bg-[#f5f6f7] text-gray-900 border-gray-300' : 'bg-white/60 text-gray-600 border-transparent hover:bg-white/80'"
            >
              {{ tab.title || tab.url || 'New Tab' }}
            </button>
          </div>
          <div class="h-10 bg-[#dadddf] flex items-center px-4 gap-2 flex-shrink-0">
            <div class="flex gap-1.5">
              <div class="w-2.5 h-2.5 rounded-full bg-red-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-yellow-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-green-400"></div>
            </div>
            <div class="flex-1 bg-white rounded-md h-6 mx-4 flex items-center px-2 shadow-inner border border-transparent">
              <Globe class="text-gray-400 flex-shrink-0" :size="12" />
              <input
                :value="previewUrl"
                class="flex-1 bg-transparent text-[10px] text-gray-700 ml-2 outline-none"
                readonly
                type="text"
                spellcheck="false"
              />
            </div>
          </div>
          <div class="flex-1 relative bg-black overflow-hidden">
            <canvas
              ref="canvasRef"
              class="w-full h-full object-contain"
            />
            <div v-if="error" class="absolute top-4 right-4 max-w-xs bg-red-500/90 text-white text-[11px] px-3 py-2 rounded-lg shadow-lg">
              {{ error }}
            </div>
          </div>
        </div>
      </main>

      <!-- Right: Status & Logs sidebar -->
      <aside class="w-[360px] flex-shrink-0 border-l border-gray-200 bg-white flex flex-col overflow-hidden">
        <div class="flex-1 overflow-y-auto p-5 space-y-5">
          <!-- Status card -->
          <div class="rounded-xl p-5 border"
            :class="[
              testing ? 'bg-purple-50 border-purple-200' :
              testDone && testSuccess ? 'bg-green-50 border-green-200' :
              testDone && !testSuccess ? 'bg-red-50 border-red-200' :
              'bg-gray-50 border-gray-200'
            ]"
          >
            <div class="flex items-center gap-3 mb-3">
              <Loader2 v-if="testing" class="text-[#831bd7] animate-spin" :size="20" />
              <CheckCircle v-else-if="testDone && testSuccess" class="text-green-500" :size="20" />
              <XCircle v-else-if="testDone && !testSuccess" class="text-red-500" :size="20" />
              <div class="w-5 h-5 rounded-full bg-gray-300" v-else></div>
              <h2 class="text-gray-900 font-bold text-base">
                {{ testing ? '正在执行...' : testDone ? (testSuccess ? '执行成功' : '执行失败') : '准备测试' }}
              </h2>
            </div>
            <p v-if="testDone && testSuccess" class="text-green-700 text-xs leading-relaxed">
              脚本已成功执行，可以保存为技能或重新执行验证。
            </p>
            <p v-else-if="testDone && !testSuccess" class="text-red-700 text-xs leading-relaxed">
              执行过程中出现错误，请查看日志后重新执行或返回修改。
            </p>
          </div>

          <!-- Action buttons — always visible after test completes -->
          <div v-if="testDone && !saved" class="flex flex-col gap-2.5">
            <button
              @click="runTest"
              :disabled="testing"
              class="flex items-center justify-center gap-2 w-full bg-white border border-gray-300 px-4 py-2.5 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              <RotateCcw :size="15" />
              重新执行
            </button>
            <button
              @click="saveSkill"
              :disabled="saving"
              class="flex items-center justify-center gap-2 w-full bg-[#831bd7] text-white px-4 py-2.5 rounded-xl text-sm font-bold hover:bg-[#7018b8] transition-colors disabled:opacity-50"
            >
              <Save :size="15" />
              {{ saving ? '保存中...' : '保存技能' }}
            </button>
            <button
              @click="goBackToRecorder"
              class="flex items-center justify-center gap-2 w-full text-gray-500 hover:text-gray-700 text-xs font-medium py-1.5 transition-colors"
            >
              重新录制
            </button>
          </div>

          <!-- Error -->
          <div v-if="error" class="bg-red-50 border border-red-200 rounded-xl p-3">
            <p class="text-red-600 text-xs">{{ error }}</p>
          </div>

          <!-- Logs -->
          <div>
            <h3 class="text-gray-900 font-bold text-sm mb-2 flex items-center gap-1.5">
              <Terminal :size="14" class="text-gray-500" />
              执行日志
            </h3>
            <div class="bg-gray-900 rounded-lg p-3 max-h-52 overflow-y-auto">
              <div
                v-for="(log, idx) in testLogs"
                :key="idx"
                class="text-[11px] font-mono text-green-400 leading-relaxed"
              >
                <span class="text-gray-600 mr-1.5">{{ String(idx + 1).padStart(2, '0') }}</span>
                {{ log }}
              </div>
              <div v-if="testOutput" class="text-[11px] font-mono text-gray-400 mt-2 border-t border-gray-700 pt-2">
                {{ testOutput }}
              </div>
              <div v-if="!testLogs.length && !testOutput" class="text-[11px] font-mono text-gray-600">
                等待执行...
              </div>
            </div>
          </div>

          <div v-if="recordedSteps.length">
            <h3 class="text-gray-900 font-bold text-sm mb-2">录制诊断</h3>
            <div class="space-y-2 max-h-64 overflow-y-auto pr-1">
              <div
                v-for="(step, index) in recordedSteps"
                :key="step.id || index"
                class="rounded-xl border border-gray-200 bg-gray-50 p-3"
              >
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Step {{ index + 1 }}</span>
                  <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                    :class="step.validation?.status === 'ok' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
                  >
                    {{ step.validation?.status || 'unknown' }}
                  </span>
                </div>
                <p class="mt-2 text-xs text-gray-700">{{ step.description || step.action }}</p>
                <p class="mt-2 text-[11px] text-gray-500 break-all">
                  <span class="font-semibold text-gray-600">Locator:</span>
                  <span class="font-mono ml-1">{{ formatLocator(step.target) }}</span>
                </p>
                <p class="mt-1 text-[11px] text-gray-500 break-all">
                  <span class="font-semibold text-gray-600">Frame:</span>
                  <span class="font-mono ml-1">{{ formatFramePath(step.frame_path) }}</span>
                </p>
                <p v-if="step.validation?.details" class="mt-1 text-[11px] text-gray-500 break-all">
                  <span class="font-semibold text-gray-600">Details:</span>
                  <span class="ml-1">{{ step.validation.details }}</span>
                </p>
              </div>
            </div>
          </div>

          <!-- Generated Script (collapsible) -->
          <div v-if="generatedScript">
            <button
              @click="showScript = !showScript"
              class="text-gray-900 font-bold text-sm mb-2 flex items-center gap-1.5 hover:text-[#831bd7] transition-colors"
            >
              <Code :size="14" class="text-gray-500" />
              {{ showScript ? '收起脚本' : '查看脚本' }}
            </button>
            <pre v-if="showScript" class="bg-gray-900 text-green-400 p-3 rounded-lg text-[11px] overflow-x-auto max-h-64 overflow-y-auto"><code>{{ generatedScript }}</code></pre>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

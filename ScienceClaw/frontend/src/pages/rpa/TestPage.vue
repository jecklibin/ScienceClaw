<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Play, Save, CheckCircle, XCircle, Loader2, Terminal, Code, ArrowLeft, RotateCcw } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { getBackendVncPageUrl, getBackendWsUrl, isLocalMode } from '@/utils/sandbox';

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

const vncPageUrl = computed(() => getBackendVncPageUrl(sessionId.value || 'sandbox', false));
const localMode = ref(isLocalMode());
const canvasRef = ref<HTMLCanvasElement | null>(null);
let screencastWs: WebSocket | null = null;

const TEST_REQUEST_TIMEOUT_MS = 210000;

const testing = ref(false);
const testDone = ref(false);
const testSuccess = ref(false);
const testOutput = ref('');
const testLogs = ref<string[]>([]);
const generatedScript = ref('');
const saving = ref(false);
const saved = ref(false);
const showScript = ref(false);
const error = ref<string | null>(null);

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
  const wsUrl = getBackendWsUrl(`/rpa/screencast/${sid}`);
  console.log('[TestPage] Connecting screencast:', wsUrl);
  screencastWs = new WebSocket(wsUrl);

  screencastWs.onopen = () => {
    console.log('[TestPage] Screencast connected');
  };

  screencastWs.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      console.log('[TestPage] Screencast message:', msg.type);
      if (msg.type === 'frame') {
        drawFrame(msg.data, msg.metadata);
      }
    } catch (e) {
      console.error('[TestPage] Parse error:', e);
    }
  };

  screencastWs.onerror = (e) => {
    console.error('[TestPage] Screencast error:', e);
  };

  screencastWs.onclose = (e) => {
    console.log('[TestPage] Screencast closed:', e.code, e.reason);
    screencastWs = null;
  };
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
    if (localMode.value) {
      await new Promise(resolve => setTimeout(resolve, 500));
      connectScreencast(sessionId.value);
    }

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
  runTest();
});

onBeforeUnmount(() => {
  if (screencastWs) {
    screencastWs.close();
    screencastWs = null;
  }
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
          <div class="h-10 bg-[#dadddf] flex items-center px-4 gap-2 flex-shrink-0">
            <div class="flex gap-1.5">
              <div class="w-2.5 h-2.5 rounded-full bg-red-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-yellow-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-green-400"></div>
            </div>
            <div class="flex-1 bg-white rounded-md h-6 mx-4 flex items-center px-3 shadow-inner">
              <Terminal class="text-gray-400" :size="12" />
              <span class="text-[10px] text-gray-600 ml-2 truncate">测试执行画面 — VNC 实时串流</span>
            </div>
          </div>
          <div class="flex-1 relative bg-black overflow-hidden">
            <iframe
              v-if="!localMode"
              :src="vncPageUrl"
              class="w-full h-full border-0 bg-black"
              allow="clipboard-read; clipboard-write"
            />
            <canvas
              v-else
              ref="canvasRef"
              class="w-full h-full object-contain"
            />
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

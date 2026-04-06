<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Settings, Code, Play, Save, ChevronRight, CheckCircle, Edit3, Tag } from 'lucide-vue-next';
import { apiClient } from '@/api/client';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => route.query.sessionId as string);
const loading = ref(true);
const error = ref<string | null>(null);

// Session data
const steps = ref<any[]>([]);
const skillName = ref('');
const skillDescription = ref('');
const generatedScript = ref('');
const showScript = ref(false);

// Auto-extracted parameters (from fill/select steps)
const params = ref<any[]>([]);

const credentials = ref<any[]>([]);

const loadCredentials = async () => {
  try {
    const resp = await apiClient.get('/credentials');
    credentials.value = resp.data.credentials || [];
  } catch {
    // Silently fail — credentials feature is optional
  }
};

// Common keyword → param name mappings (Chinese & English)
const KEYWORD_MAP: Record<string, string> = {
  '邮箱': 'email', '邮件': 'email', 'email': 'email', 'e-mail': 'email',
  '密码': 'password', 'password': 'password', 'pwd': 'password',
  '用户名': 'username', '用户': 'username', 'username': 'username', 'user': 'username',
  '账号': 'account', 'account': 'account',
  '手机': 'phone', '电话': 'phone', 'phone': 'phone', 'tel': 'phone', 'mobile': 'phone',
  '验证码': 'captcha', 'captcha': 'captcha', 'code': 'code',
  '搜索': 'search', 'search': 'search',
  '地址': 'address', 'address': 'address', 'url': 'url',
  '姓名': 'name', 'name': 'name',
};

/**
 * Derive a semantic parameter name from the locator and sensitivity.
 * Returns snake_case identifier or empty string on failure.
 */
function deriveParamName(loc: any, sensitive: boolean): string {
  if (!loc) return sensitive ? 'password' : '';

  // For password fields, always use 'password'
  if (sensitive) return 'password';

  // Collect candidate texts from locator (in priority order)
  const candidates: string[] = [];
  if (loc.name) candidates.push(loc.name);           // accessible name (e.g. "邮箱")
  if (loc.value && loc.method !== 'css') candidates.push(loc.value); // placeholder/label text
  if (loc.role) candidates.push(loc.role);            // fallback to role (e.g. "textbox")

  for (const text of candidates) {
    const lower = text.toLowerCase().trim();
    // Try direct keyword match
    for (const [keyword, paramName] of Object.entries(KEYWORD_MAP)) {
      if (lower.includes(keyword)) return paramName;
    }
    // If text is a short ASCII identifier already, use it directly
    const ascii = lower.replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
    if (ascii && ascii.length >= 2 && ascii.length <= 30 && /^[a-z]/.test(ascii)) {
      return ascii;
    }
  }
  return '';
}

const loadSession = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId 参数';
    loading.value = false;
    return;
  }
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = resp.data.session;
    steps.value = session.steps || [];

    // Auto-extract parameters from fill/select steps
    const usedNames = new Set<string>();
    params.value = steps.value
      .filter((s: any) => s.action === 'fill' || s.action === 'select')
      .map((s: any, i: number) => {
        let label = `参数${i + 1}`;
        let semanticName = '';
        try {
          const loc = typeof s.target === 'string' ? JSON.parse(s.target) : s.target;
          // Extract display label from locator
          if (loc?.name) label = loc.name;
          else if (loc?.value) label = loc.value;
          // Derive semantic param name from locator context
          semanticName = deriveParamName(loc, s.sensitive);
        } catch { /* use default */ }
        // Ensure unique name
        let name = semanticName || `param_${i}`;
        if (usedNames.has(name)) {
          let suffix = 2;
          while (usedNames.has(`${name}_${suffix}`)) suffix++;
          name = `${name}_${suffix}`;
        }
        usedNames.add(name);
        return {
          id: `param_${i}`,
          name,
          label,
          original_value: s.value || '',
          current_value: s.value || '',
          enabled: true,
          step_id: s.id,
          sensitive: s.sensitive || false,
          credential_id: '',
        };
      });

    // Auto-generate skill name from first navigate step
    const navStep = steps.value.find((s: any) => s.url);
    if (navStep) {
      try {
        const url = new URL(navStep.url);
        skillName.value = `${url.hostname} 自动化`;
      } catch {
        skillName.value = '录制技能';
      }
    } else {
      skillName.value = '录制技能';
    }
    skillDescription.value = `自动执行 ${steps.value.length} 个录制步骤`;
  } catch (err: any) {
    error.value = '加载会话失败: ' + (err.response?.data?.detail || err.message);
  } finally {
    loading.value = false;
  }
};

const generateScript = async () => {
  try {
    const paramMap: Record<string, any> = {};
    params.value.filter(p => p.enabled).forEach(p => {
      paramMap[p.name] = {
        original_value: p.original_value,
        sensitive: p.sensitive || false,
        credential_id: p.credential_id || '',
      };
    });

    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/generate`, {
      params: paramMap,
    });
    generatedScript.value = resp.data.script;
    showScript.value = true;
  } catch (err: any) {
    error.value = '生成脚本失败: ' + (err.response?.data?.detail || err.message);
  }
};

const goToTest = () => {
  const paramMap: Record<string, any> = {};
  params.value.filter(p => p.enabled).forEach(p => {
    paramMap[p.name] = {
      original_value: p.original_value,
      sensitive: p.sensitive || false,
      credential_id: p.credential_id || '',
    };
  });
  router.push({
    path: '/rpa/test',
    query: {
      sessionId: sessionId.value,
      skillName: skillName.value,
      skillDescription: skillDescription.value,
      params: JSON.stringify(paramMap),
    },
  });
};

const getActionLabel = (action: string) => {
  const map: Record<string, string> = {
    click: '点击',
    fill: '输入',
    press: '按键',
    select: '选择',
    navigate: '导航',
  };
  return map[action] || action;
};

const getActionColor = (action: string) => {
  const map: Record<string, string> = {
    click: 'bg-blue-100 text-blue-700',
    fill: 'bg-green-100 text-green-700',
    press: 'bg-yellow-100 text-yellow-700',
    select: 'bg-purple-100 text-purple-700',
    navigate: 'bg-orange-100 text-orange-700',
  };
  return map[action] || 'bg-gray-100 text-gray-700';
};

onMounted(() => {
  loadSession();
  loadCredentials();
});
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7]">
    <!-- Header -->
    <header class="h-16 bg-white border-b border-gray-200 flex items-center px-8 gap-4">
      <Settings class="text-[#831bd7]" :size="24" />
      <h1 class="text-gray-900 font-extrabold text-xl">配置技能</h1>
      <div class="flex-1"></div>
      <button
        @click="generateScript"
        class="flex items-center gap-2 bg-white border border-gray-300 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50"
      >
        <Code :size="16" />
        预览脚本
      </button>
      <button
        @click="goToTest"
        class="flex items-center gap-2 bg-[#831bd7] text-white px-6 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8] transition-colors"
      >
        <Play :size="16" />
        开始测试
        <ChevronRight :size="16" />
      </button>
    </header>

    <div v-if="loading" class="flex items-center justify-center h-64">
      <p class="text-gray-500">加载中...</p>
    </div>

    <div v-else-if="error" class="flex items-center justify-center h-64">
      <p class="text-red-500">{{ error }}</p>
    </div>

    <div v-else class="max-w-6xl mx-auto p-8 space-y-8">
      <!-- Skill Info -->
      <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <h2 class="text-gray-900 font-bold text-lg mb-4">技能信息</h2>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">技能名称</label>
            <input
              v-model="skillName"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] focus:border-transparent outline-none"
            />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">描述</label>
            <input
              v-model="skillDescription"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] focus:border-transparent outline-none"
            />
          </div>
        </div>
      </div>

      <!-- Steps -->
      <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <h2 class="text-gray-900 font-bold text-lg mb-4">录制步骤 ({{ steps.length }})</h2>
        <div class="space-y-3">
          <div
            v-for="(step, idx) in steps"
            :key="step.id"
            class="flex items-center gap-4 p-3 bg-gray-50 rounded-lg"
          >
            <span class="text-xs text-gray-400 font-mono w-6">{{ idx + 1 }}</span>
            <span
              class="text-[10px] font-bold px-2 py-0.5 rounded"
              :class="getActionColor(step.action)"
            >
              {{ getActionLabel(step.action) }}
            </span>
            <span class="text-sm text-gray-700 flex-1 truncate">
              {{ step.description || `${step.action} → ${step.target || step.label || ''}` }}
            </span>
            <span v-if="step.value" class="text-xs text-gray-400 font-mono truncate max-w-[200px]">
              "{{ step.sensitive ? '*****' : step.value }}"
            </span>
          </div>
          <div v-if="steps.length === 0" class="text-center text-gray-400 py-8">
            没有录制到步骤
          </div>
        </div>
      </div>

      <!-- Parameters -->
      <div v-if="params.length > 0" class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <h2 class="text-gray-900 font-bold text-lg mb-4">
          <Tag :size="18" class="inline mr-2 text-[#831bd7]" />
          可配置参数
        </h2>
        <p class="text-xs text-gray-500 mb-4">以下输入值可以参数化，运行时动态传入。</p>
        <div class="space-y-3">
          <div
            v-for="param in params"
            :key="param.id"
            class="flex items-center gap-4 p-3 bg-gray-50 rounded-lg"
          >
            <input
              type="checkbox"
              v-model="param.enabled"
              class="accent-[#831bd7]"
            />
            <div class="flex-1">
              <input
                v-model="param.name"
                class="text-sm font-medium text-gray-700 bg-transparent border-b border-dashed border-gray-300 focus:border-[#831bd7] outline-none w-40"
                placeholder="参数名"
              />
              <p class="text-[10px] text-gray-400 mt-1">{{ param.label }}</p>
            </div>
            <template v-if="param.sensitive">
              <select
                v-model="param.credential_id"
                class="text-sm text-gray-600 border border-gray-200 rounded px-2 py-1 w-48"
              >
                <option value="">选择凭据...</option>
                <option
                  v-for="cred in credentials"
                  :key="cred.id"
                  :value="cred.id"
                >
                  {{ cred.name }} ({{ cred.username }})
                </option>
              </select>
            </template>
            <template v-else>
              <input
                v-model="param.current_value"
                class="text-sm text-gray-600 border border-gray-200 rounded px-2 py-1 w-48"
                placeholder="默认值"
              />
            </template>
          </div>
        </div>
      </div>

      <!-- Script Preview -->
      <div v-if="showScript" class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <h2 class="text-gray-900 font-bold text-lg mb-4">
          <Code :size="18" class="inline mr-2 text-[#831bd7]" />
          生成的 Playwright 脚本
        </h2>
        <pre class="bg-gray-900 text-green-400 p-4 rounded-lg text-xs overflow-x-auto max-h-96 overflow-y-auto"><code>{{ generatedScript }}</code></pre>
      </div>
    </div>
  </div>
</template>

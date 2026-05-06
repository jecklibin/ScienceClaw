<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  Settings,
  Code,
  Tag,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import RpaDiscardRecordingDialog from '@/components/rpa/RpaDiscardRecordingDialog.vue';
import RpaFlowGuide from '@/components/rpa/RpaFlowGuide.vue';
import RpaStepTimeline from '@/components/rpa/RpaStepTimeline.vue';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';
import {
  getManualRecordingDiagnostics,
  getLegacyRpaSteps,
  mapRpaConfigureDisplaySteps,
  type RpaRecordingDiagnosticItem,
  type RpaConfigureStep,
} from '@/utils/rpaConfigureTimeline';
import {
  formatRpaActionLabel,
  formatRpaStepLocator,
} from '@/utils/rpaStepTimeline';
import {
  buildRpaSkillConfigDraft,
  draftParamsToParamItems,
  paramItemsToDraftParams,
  type RpaConfigParamItem,
  type RpaSkillConfigDraft,
} from '@/utils/rpaSkillConfigDraft';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const loadFailed = ref(false);
const error = ref<string | null>(null);

interface ParsedLocator {
  method?: string;
  role?: string;
  name?: string;
  value?: string;
  parent?: ParsedLocator;
  child?: ParsedLocator;
  base?: ParsedLocator;
  index?: number;
  locator?: ParsedLocator;
}

interface LocatorCandidate {
  kind?: string;
  score?: number;
  selected?: boolean;
  reason?: string;
  strict_match_count?: number;
  visible_match_count?: number;
  locator?: ParsedLocator | string | null;
}

interface StepValidation {
  status?: string;
  details?: string;
}

interface StepItem extends RpaConfigureStep {
  id: string;
  action: string;
  target?: ParsedLocator | string | null;
  frame_path?: string[];
  locator_candidates?: LocatorCandidate[];
  validation?: StepValidation;
  value?: string;
  description?: string;
  label?: string;
  sensitive?: boolean;
  url?: string;
  source?: string;
  configurable?: boolean;
}

interface ParamItem extends RpaConfigParamItem {}

interface CredentialItem {
  id: string;
  name?: string;
  username?: string;
}

const steps = ref<StepItem[]>([]);
const legacySteps = ref<StepItem[]>([]);
const diagnostics = ref<RpaRecordingDiagnosticItem[]>([]);
const skillName = ref('');
const skillDescription = ref('');
const generatedScript = ref('');
const scriptGenerating = ref(false);
const params = ref<ParamItem[]>([]);
const credentials = ref<CredentialItem[]>([]);
const promotingStepIndex = ref<number | null>(null);
const isScriptDrawerOpen = ref(false);
const hasDiagnostics = computed(() => diagnostics.value.length > 0);
const isDiscardDialogOpen = ref(false);

const parseLocator = (raw: unknown): ParsedLocator | null => {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return { method: 'css', value: raw };
    }
  }
  return raw as ParsedLocator;
};

const VALIDATION_LABELS: Record<string, string> = {
  ok: 'Strict match',
  ambiguous: 'Ambiguous / not unique',
  fallback: 'Fallback',
  warning: 'Warning',
  broken: 'Broken',
};

const VALIDATION_CLASS_MAP: Record<string, string> = {
  ok: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400 ring-1 ring-emerald-200 dark:ring-emerald-800',
  ambiguous: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  fallback: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  warning: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  broken: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400 ring-1 ring-rose-200 dark:ring-rose-800',
};

const getValidationLabel = (status?: string) => {
  if (!status) return 'Unknown';
  return VALIDATION_LABELS[status] || status.replace(/_/g, ' ');
};

const getValidationClass = (status?: string) => {
  if (!status) return 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
  return VALIDATION_CLASS_MAP[status] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
};

const promoteLocator = async (stepIndex: number, candidateIndex: number, step: StepItem = steps.value[stepIndex]) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = stepIndex;
  error.value = null;
  try {
    if (step?.traceId) {
      await apiClient.post(`/rpa/session/${sessionId.value}/trace/${step.traceId}/locator`, {
        candidate_index: candidateIndex,
      });
    } else {
      await apiClient.post(`/rpa/session/${sessionId.value}/step/${stepIndex}/locator`, {
        candidate_index: candidateIndex,
      });
    }
    await loadSession();
  } catch (err: any) {
    error.value = `切换定位器失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    promotingStepIndex.value = null;
  }
};

const promoteDiagnosticLocator = async (diagnostic: RpaRecordingDiagnosticItem, candidateIndex: number) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  if (diagnostic.traceId) {
    const promotionIndex = diagnostic.stepIndex ?? -1;
    promotingStepIndex.value = promotionIndex;
    error.value = null;
    try {
      await apiClient.post(`/rpa/session/${sessionId.value}/trace/${diagnostic.traceId}/locator`, {
        candidate_index: candidateIndex,
      });
      await loadSession();
    } catch (err: any) {
      error.value = `鍒囨崲瀹氫綅鍣ㄥけ璐? ${err.response?.data?.detail || err.message}`;
    } finally {
      promotingStepIndex.value = null;
    }
    return;
  }
  if (diagnostic.stepIndex === null) return;
  await promoteLocator(diagnostic.stepIndex, candidateIndex);
};

const deleteDiagnosticStep = async (diagnostic: RpaRecordingDiagnosticItem) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  if (!diagnostic.diagnosticId && diagnostic.stepIndex === null) return;
  promotingStepIndex.value = diagnostic.stepIndex ?? -1;
  error.value = null;
  try {
    if (diagnostic.diagnosticId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/diagnostic/${diagnostic.diagnosticId}`);
    } else {
      await apiClient.delete(`/rpa/session/${sessionId.value}/step/${diagnostic.stepIndex}`);
    }
    await loadSession();
    if (!diagnostics.value.length && !generatedScript.value) {
      await generateScript({ openDrawer: false });
    }
  } catch (err: any) {
    error.value = `删除待修复步骤失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    promotingStepIndex.value = null;
  }
};

const loadCredentials = async () => {
  try {
    const resp = await apiClient.get('/credentials');
    credentials.value = resp.data.credentials || [];
  } catch {
    // Credentials are optional for this page.
  }
};

const loadSkillConfigDraft = async (generatedParams: ParamItem[]) => {
  if (!sessionId.value) return false;
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}/skill-config-draft`);
    const draft = resp.data.draft as RpaSkillConfigDraft | null;
    if (!draft) return false;
    skillName.value = draft.skill_name || skillName.value;
    skillDescription.value = draft.description || skillDescription.value;
    params.value = draftParamsToParamItems(draft.params || {}, generatedParams) as ParamItem[];
    return true;
  } catch {
    return false;
  }
};

const KEYWORD_MAP: Record<string, string> = {
  邮箱: 'email', 邮件: 'email', email: 'email', 'e-mail': 'email',
  密码: 'password', password: 'password', pwd: 'password',
  用户名: 'username', 用户: 'username', username: 'username', user: 'username',
  账号: 'account', account: 'account',
  手机: 'phone', 电话: 'phone', phone: 'phone', tel: 'phone', mobile: 'phone',
  验证码: 'captcha', captcha: 'captcha', code: 'code',
  搜索: 'search', search: 'search',
  地址: 'address', address: 'address', url: 'url',
  姓名: 'name', name: 'name',
};

function deriveParamName(loc: ParsedLocator | null, sensitive: boolean): string {
  if (!loc) return sensitive ? 'password' : '';
  if (sensitive) return 'password';

  const candidates: string[] = [];
  if (loc.name) candidates.push(loc.name);
  if (loc.value && loc.method !== 'css') candidates.push(loc.value);
  if (loc.role) candidates.push(loc.role);

  for (const text of candidates) {
    const lower = text.toLowerCase().trim();
    for (const [keyword, paramName] of Object.entries(KEYWORD_MAP)) {
      if (lower.includes(keyword)) return paramName;
    }

    const ascii = lower
      .replace(/[^a-z0-9_]/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '');
    if (ascii && ascii.length >= 2 && ascii.length <= 30 && /^[a-z]/.test(ascii)) {
      return ascii;
    }
  }
  return '';
}

const loadSession = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId 参数';
    loadFailed.value = true;
    loading.value = false;
    return;
  }

  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = {
      ...(resp.data.session || {}),
      timeline: resp.data.session?.timeline ?? resp.data.timeline,
    };
    legacySteps.value = getLegacyRpaSteps(session) as StepItem[];
    steps.value = mapRpaConfigureDisplaySteps(session) as StepItem[];
    diagnostics.value = getManualRecordingDiagnostics(session);
    loadFailed.value = false;
    error.value = null;

    const usedNames = new Set<string>();
    const paramSourceSteps = Array.isArray(session.timeline) ? steps.value : legacySteps.value;
    const generatedParams = paramSourceSteps
      .filter((step) => step.action === 'fill' || step.action === 'select')
      .map((step, index) => {
        let label = `参数${index + 1}`;
        let semanticName = '';

        try {
          const loc = parseLocator(step.target);
          if (loc?.name) label = loc.name;
          else if (loc?.value) label = loc.value;
          semanticName = deriveParamName(loc, !!step.sensitive);
        } catch {
          // Fall back to generated defaults.
        }

        let name = semanticName || `param_${index}`;
        if (usedNames.has(name)) {
          let suffix = 2;
          while (usedNames.has(`${name}_${suffix}`)) suffix++;
          name = `${name}_${suffix}`;
        }
        usedNames.add(name);

        return {
          id: `param_${index}`,
          name,
          label,
          original_value: step.value || '',
          default_value: step.value || '',
          enabled: true,
          step_id: step.id,
          sensitive: !!step.sensitive,
          credential_id: '',
        };
      });
    params.value = generatedParams;

    const navStep = steps.value.find((step) => !!step.url) || legacySteps.value.find((step) => !!step.url);
    if (navStep?.url) {
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
    error.value = `加载会话失败: ${err.response?.data?.detail || err.message}`;
    if (!steps.value.length) loadFailed.value = true;
  } finally {
    loading.value = false;
  }
};

const buildParamMap = () => paramItemsToDraftParams(params.value);

const saveSkillConfigDraft = async () => {
  if (!sessionId.value) return;
  await apiClient.put(
    `/rpa/session/${sessionId.value}/skill-config-draft`,
    buildRpaSkillConfigDraft({
      skillName: skillName.value,
      skillDescription: skillDescription.value,
      params: params.value,
    }),
  );
};

const generateScript = async (options: { openDrawer?: boolean } | Event = { openDrawer: true }) => {
  const resolvedOptions = options instanceof Event ? { openDrawer: true } : options;
  if (hasDiagnostics.value) {
    generatedScript.value = '';
    isScriptDrawerOpen.value = false;
    error.value = `还有 ${diagnostics.value.length} 个待修复步骤，修复后才能生成脚本`;
    return;
  }
  try {
    scriptGenerating.value = true;
    error.value = null;
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/generate`, {
      params: buildParamMap(),
    });
    generatedScript.value = resp.data.script || '';
    isScriptDrawerOpen.value = resolvedOptions.openDrawer !== false;
  } catch (err: any) {
    isScriptDrawerOpen.value = false;
    generatedScript.value = '';
    error.value = `生成脚本失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    scriptGenerating.value = false;
  }
};

const goToTest = async () => {
  if (hasDiagnostics.value) {
    error.value = `还有 ${diagnostics.value.length} 个待修复步骤，修复后才能开始测试`;
    return;
  }
  try {
    await saveSkillConfigDraft();
    router.push({
      path: '/rpa/test',
      query: {
        sessionId: sessionId.value,
      },
    });
  } catch (err: any) {
    error.value = `淇濆瓨閰嶇疆鑽夌澶辫触: ${err.response?.data?.detail || err.message}`;
  }
};

const confirmDiscardAndRecord = () => {
  isDiscardDialogOpen.value = true;
};

const startNewRecording = () => {
  router.push('/rpa/recorder');
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const goToMcpToolEditor = () => {
  router.push(buildRpaToolEditorLocation({
    sessionId: sessionId.value,
    skillName: skillName.value,
    skillDescription: skillDescription.value,
  }));
};

const handleSecondaryAction = (id: string) => {
  if (id === 'preview-script') {
    generateScript();
    return;
  }
  if (id === 'convert-mcp') {
    goToMcpToolEditor();
  }
};

onMounted(async () => {
  await loadSession();
  if (!loadFailed.value) {
    await loadSkillConfigDraft(params.value);
  }
  loadCredentials();
  if (!loadFailed.value && sessionId.value && !hasDiagnostics.value) {
    await generateScript({ openDrawer: false });
  }
});
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7] dark:bg-[#161618] text-gray-900 dark:text-gray-100">
    <RpaFlowGuide
      class="sticky top-0 z-30"
      current-step="configure"
      :session-id="sessionId"
      :recorded-step-count="steps.length"
      :diagnostic-count="diagnostics.length"
      :skill-name="skillName"
      primary-label="开始测试"
      :primary-disabled="hasDiagnostics"
      :secondary-actions="[
        { id: 'convert-mcp', label: '转换为 MCP 工具', tone: 'accent' },
        { id: 'preview-script', label: scriptGenerating ? '生成中...' : '预览脚本', disabled: scriptGenerating || hasDiagnostics },
      ]"
      @home="goToHome"
      @skills="goToSkills"
      @go-record="confirmDiscardAndRecord"
      @go-test="goToTest"
      @primary-action="goToTest"
      @secondary-action="handleSecondaryAction"
    />

    <RpaDiscardRecordingDialog
      v-model:open="isDiscardDialogOpen"
      @confirm="startNewRecording"
    />

    <div v-if="loading" class="flex h-64 items-center justify-center">
      <p class="text-sm text-gray-500 dark:text-gray-400">加载中...</p>
    </div>

    <div v-else-if="loadFailed" class="flex h-64 items-center justify-center px-6">
      <div class="rounded-2xl border border-rose-200 bg-white dark:bg-[#272728] px-6 py-5 text-sm text-rose-600 shadow-sm">
        {{ error || '页面加载失败' }}
      </div>
    </div>

    <main v-else class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <div v-if="error" class="mb-4 rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/30 px-4 py-3 text-sm text-amber-800">
        {{ error }}
      </div>

      <div class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section class="space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-xl font-extrabold tracking-tight">录制步骤</h2>
              <p class="text-sm text-gray-500 dark:text-gray-400">默认显示摘要信息，点击任一步骤可展开查看详细定位诊断。</p>
            </div>
            <div class="rounded-full bg-white dark:bg-[#272728] px-4 py-1.5 text-xs font-bold text-[#831bd7] shadow-sm ring-1 ring-[#831bd7]/10">
              共 {{ steps.length }} 步
            </div>
          </div>

          <section
            v-if="diagnostics.length"
            class="rounded-3xl border border-rose-200 dark:border-rose-900/60 bg-rose-50/80 dark:bg-rose-950/20 p-4 shadow-sm"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <h3 class="text-sm font-bold text-rose-700 dark:text-rose-300">待修复步骤</h3>
                <p class="mt-1 text-xs text-rose-600 dark:text-rose-300/80">
                  这些步骤还没有形成稳定可回放的事实，修复或删除后才能生成脚本。
                </p>
              </div>
              <span class="rounded-full bg-white/80 dark:bg-rose-950/40 px-3 py-1 text-[11px] font-bold text-rose-700 dark:text-rose-300 ring-1 ring-rose-200 dark:ring-rose-900/60">
                {{ diagnostics.length }} 个待处理
              </span>
            </div>

            <div class="mt-4 space-y-3">
              <article
                v-for="diagnostic in diagnostics"
                :key="diagnostic.id"
                class="rounded-2xl border border-rose-200/80 dark:border-rose-900/60 bg-white dark:bg-[#272728] p-4"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="rounded-full bg-rose-100 dark:bg-rose-900/40 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-rose-700 dark:text-rose-300">
                        {{ formatRpaActionLabel(diagnostic.action) }}
                      </span>
                      <span
                        class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                        :class="getValidationClass(diagnostic.validation.status)"
                      >
                        {{ getValidationLabel(diagnostic.validation.status) }}
                      </span>
                    </div>
                    <h4 class="mt-2 text-sm font-bold text-gray-900 dark:text-gray-100">
                      {{ diagnostic.description }}
                    </h4>
                    <p class="mt-1 text-xs text-rose-700 dark:text-rose-300/80">
                      {{ diagnostic.validation.details }}
                    </p>
                    <p v-if="diagnostic.url" class="mt-2 break-all font-mono text-[11px] text-gray-500 dark:text-gray-400">
                      {{ diagnostic.url }}
                    </p>
                  </div>
                  <button
                    type="button"
                    class="shrink-0 rounded-full border border-rose-200 dark:border-rose-900/60 px-3 py-1.5 text-xs font-semibold text-rose-700 dark:text-rose-300 transition-colors hover:bg-rose-100/80 dark:hover:bg-rose-900/30 disabled:cursor-not-allowed disabled:opacity-60"
                    :disabled="(!diagnostic.diagnosticId && diagnostic.stepIndex === null) || promotingStepIndex === (diagnostic.stepIndex ?? -1)"
                    @click="deleteDiagnosticStep(diagnostic)"
                  >
                    {{ promotingStepIndex === (diagnostic.stepIndex ?? -1) ? '处理中...' : '删除该步' }}
                  </button>
                </div>

                <div v-if="diagnostic.locator_candidates?.length" class="mt-4 space-y-2">
                  <div
                    v-for="(candidate, candidateIndex) in diagnostic.locator_candidates"
                    :key="`${diagnostic.id}-${candidateIndex}`"
                    class="flex flex-col gap-2 rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-3 md:flex-row md:items-start md:justify-between"
                  >
                    <div class="min-w-0 flex-1">
                      <div class="flex flex-wrap items-center gap-2 text-[11px]">
                        <span class="rounded-full bg-gray-100 dark:bg-[#444345] px-2 py-0.5 font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
                          {{ candidate.kind || 'locator' }}
                        </span>
                        <span v-if="candidate.playwright_locator" class="text-gray-400 dark:text-gray-500">Playwright</span>
                        <span v-if="candidate.selector" class="text-gray-400 dark:text-gray-500">Selector</span>
                      </div>
                      <p class="mt-1 break-all font-mono text-xs text-gray-700 dark:text-gray-300">
                        {{ formatRpaStepLocator(candidate.locator) }}
                      </p>
                      <p v-if="candidate.reason" class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">{{ candidate.reason }}</p>
                    </div>

                    <button
                      type="button"
                      class="shrink-0 rounded-full border border-[#831bd7]/25 px-3 py-1.5 text-xs font-semibold text-[#831bd7] transition-colors hover:bg-[#831bd7]/5 disabled:cursor-not-allowed disabled:opacity-60"
                      :disabled="(!diagnostic.traceId && diagnostic.stepIndex === null) || promotingStepIndex === (diagnostic.stepIndex ?? -1)"
                      @click="promoteDiagnosticLocator(diagnostic, candidateIndex)"
                    >
                      {{ promotingStepIndex === (diagnostic.stepIndex ?? -1) ? '切换中...' : '使用此定位器' }}
                    </button>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <RpaStepTimeline
            class="min-h-[620px] overflow-hidden rounded-2xl"
            :steps="steps"
            mode="configure"
            :show-header="false"
            :show-candidates="true"
            :promoting-step-index="promotingStepIndex"
            empty-message="当前没有可配置的录制步骤。"
            @promote-locator="promoteLocator($event.stepIndex, $event.candidateIndex, $event.step)"
          />
        </section>

        <aside class="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <section class="rounded-3xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-5 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Settings :size="18" />
              </div>
              <div>
                <h2 class="text-base font-extrabold">技能信息</h2>
              </div>
            </div>

            <div class="mt-4 space-y-4">
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500 dark:text-gray-400">技能名称</label>
                <input
                  v-model="skillName"
                  class="w-full rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500 dark:text-gray-400">描述</label>
                <textarea
                  v-model="skillDescription"
                  rows="3"
                  class="w-full resize-none rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-5 shadow-sm">
            <div class="flex items-start gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Tag :size="18" />
              </div>
              <div class="min-w-0">
                <h2 class="text-base font-extrabold">可配置参数</h2>
              </div>
            </div>

            <div v-if="params.length > 0" class="mt-4 max-h-[calc(100vh-22rem)] space-y-3 overflow-y-auto pr-1">
              <div
                v-for="param in params"
                :key="param.id"
                class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] p-3"
              >
                <div class="flex items-center gap-3">
                  <input
                    v-model="param.enabled"
                    type="checkbox"
                    class="h-4 w-4 rounded border-gray-300 dark:border-gray-600 accent-[#831bd7]"
                  />
                  <input
                    v-model="param.name"
                    class="min-w-0 flex-1 border-0 bg-transparent text-sm font-semibold text-gray-800 dark:text-gray-200 outline-none"
                    placeholder="参数名"
                  />
                  <span
                    class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    :class="param.sensitive ? 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-700 dark:text-fuchsia-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300'"
                  >
                    {{ param.sensitive ? '敏感' : '普通' }}
                  </span>
                </div>

                <p class="mt-2 text-[11px] text-gray-500 dark:text-gray-400">{{ param.label }}</p>

                <div class="mt-3">
                  <select
                    v-if="param.sensitive"
                    v-model="param.credential_id"
                    class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] px-3 py-2 text-sm text-gray-700 dark:text-gray-300 outline-none transition-colors focus:border-[#831bd7]"
                  >
                    <option value="">选择凭据...</option>
                    <option
                      v-for="cred in credentials"
                      :key="cred.id"
                      :value="cred.id"
                    >
                      {{ cred.name || '未命名凭据' }}{{ cred.username ? ` (${cred.username})` : '' }}
                    </option>
                  </select>
                  <input
                    v-else
                    v-model="param.default_value"
                    class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] px-3 py-2 text-sm text-gray-700 dark:text-gray-300 outline-none transition-colors focus:border-[#831bd7]"
                    placeholder="默认值"
                  />
                </div>
              </div>
            </div>

            <div v-else class="mt-4 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              当前没有可参数化的输入步骤。
            </div>
          </section>
        </aside>
      </div>
    </main>

    <Dialog :open="isScriptDrawerOpen" @update:open="(open: boolean) => { isScriptDrawerOpen = open }">
      <DialogContent
        class="left-auto right-0 top-0 h-screen w-[min(760px,100vw)] max-h-none max-w-none translate-x-0 translate-y-0 overflow-hidden rounded-none border-l border-gray-200 dark:border-gray-700 bg-[#0f1115] p-0"
      >
        <div class="flex h-full flex-col">
          <DialogHeader class="border-b border-white/10 px-6 py-4 text-left">
            <DialogTitle class="flex items-center gap-2 text-base font-bold text-white">
              <Code :size="18" />
              脚本预览
            </DialogTitle>
          </DialogHeader>

          <div class="flex-1 overflow-auto px-6 py-5">
            <pre class="min-h-full overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs leading-6 text-emerald-300"><code>{{ generatedScript }}</code></pre>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>

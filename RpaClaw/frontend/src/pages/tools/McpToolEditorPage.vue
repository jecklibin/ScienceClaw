<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, Beaker, ChevronDown, ChevronUp, Save, Shield, Wand2 } from 'lucide-vue-next';

import {
  createRpaMcpTool,
  previewRpaMcpTool,
  testPreviewRpaMcpTool,
  type JsonSchemaObject,
  type RpaMcpExecutionResult,
  type RpaMcpPreview,
} from '@/api/rpaMcp';
import { apiClient } from '@/api/client';
import {
  buildRpaRecorderLocation,
  buildPreviewDraftSignature,
  focusPreviewTestSection,
  getPreviewTestStatus,
  hasMatchingPreviewTest,
} from '@/utils/rpaMcpConvert';
import { convertCookieInputToPlaywrightCookies, type CookieInputMode } from '@/utils/rpaMcpTest';
import { showErrorToast, showSuccessToast } from '@/utils/toast';

type GatewayParamField = {
  key: string;
  type: string;
  description: string;
  required: boolean;
  defaultValue?: unknown;
};

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

interface RecordedStepItem {
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
}

const route = useRoute();
const router = useRouter();
const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const preview = ref<RpaMcpPreview | null>(null);
const testResult = ref<RpaMcpExecutionResult | null>(null);
const hasSuccessfulTest = ref(false);
const lastSuccessfulTestSignature = ref<string | null>(null);
const recordedSteps = ref<RecordedStepItem[]>([]);
const stepsLoading = ref(false);
const promotingStepIndex = ref<number | null>(null);
const expandedStepIndex = ref<number | null>(null);
const toolName = ref('');
const description = ref('');
const postAuthStartUrl = ref('');
const allowedDomainsText = ref('');
const outputSchemaText = ref('{}');
const cookieSectionOpen = ref(false);
const cookieMode = ref<CookieInputMode>('cookie_header');
const cookieText = ref('');
const cookieDomain = ref('');
const previewTestSection = ref<HTMLElement | null>(null);
const argumentValues = reactive<Record<string, unknown>>({});
const source = computed(() => typeof route.query.source === 'string' ? route.query.source : '');
const formatJsonBlock = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

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

const shortenText = (value: string, max = 48): string => {
  if (!value) return '';
  return value.length > max ? `${value.slice(0, Math.max(0, max - 3))}...` : value;
};

const getNthBaseLocator = (locator: ParsedLocator) => locator.locator || locator.base;

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
    const baseLocator = getNthBaseLocator(locator);
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

const getActionLabel = (action: string) => {
  const map: Record<string, string> = {
    click: 'Click',
    fill: 'Fill',
    press: 'Press',
    select: 'Select',
    navigate: 'Navigate',
    goto: 'Navigate',
    navigate_click: 'Navigate after click',
    navigate_press: 'Navigate after keypress',
    open_tab_click: 'Open tab',
    switch_tab: 'Switch tab',
    close_tab: 'Close tab',
    download_click: 'Download',
    download: 'Download',
  };
  return map[action] || action;
};

const getActionColor = (action: string) => {
  const map: Record<string, string> = {
    click: 'bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400',
    fill: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400',
    press: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400',
    select: 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-700 dark:text-fuchsia-400',
    navigate: 'bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-400',
    goto: 'bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-400',
    navigate_click: 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-400',
    navigate_press: 'bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-400',
    open_tab_click: 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-400',
    switch_tab: 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300',
    close_tab: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400',
    download_click: 'bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-400',
    download: 'bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-400',
  };
  return map[action] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300';
};

const getValuePreview = (step: RecordedStepItem) => {
  if (!step.value) return '';
  const display = step.sensitive ? '******' : String(step.value);
  return shortenText(`Value: ${display}`, 28);
};

const getFrameHint = (step: RecordedStepItem) => {
  if (!step.frame_path?.length) return '';
  return `iframe ${step.frame_path.length} level${step.frame_path.length > 1 ? 's' : ''}`;
};

const getSelectedCandidate = (step: RecordedStepItem): LocatorCandidate | null => {
  const candidates = step.locator_candidates || [];
  return candidates.find((candidate) => candidate.selected) || candidates[0] || null;
};

const formatCandidateMatchText = (candidate: LocatorCandidate): string => {
  const strictCount = candidate.strict_match_count;
  const visibleCount = candidate.visible_match_count;

  if (typeof strictCount === 'number' && strictCount > 0) {
    return strictCount === 1 ? 'strict match' : `${strictCount} strict matches`;
  }
  if (typeof visibleCount === 'number') {
    return `${visibleCount} visible match${visibleCount === 1 ? '' : 'es'}`;
  }
  if (typeof strictCount === 'number') {
    return `${strictCount} strict match${strictCount === 1 ? '' : 'es'}`;
  }
  return '';
};

const getCandidateSummary = (step: RecordedStepItem) => {
  const candidates = step.locator_candidates || [];
  const total = candidates.length;
  if (!total) return '';
  const selected = getSelectedCandidate(step);
  if (!selected) return `${total} candidate${total === 1 ? '' : 's'}`;

  const summary: string[] = [];
  if (selected.kind) summary.push(`Current ${selected.kind}`);
  const matchText = formatCandidateMatchText(selected);
  if (matchText) summary.push(matchText);
  summary.push(`${total} candidate${total === 1 ? '' : 's'}`);
  return summary.join(' / ');
};

const getStepTitle = (step: RecordedStepItem) => {
  if (step.description) return step.description;
  return `${getActionLabel(step.action)} ${formatLocator(step.target || step.label || '')}`;
};

const getStepLocatorSummary = (step: RecordedStepItem) => shortenText(formatLocator(step.target || step.label || ''), 72);

const toggleStep = (index: number) => {
  expandedStepIndex.value = expandedStepIndex.value === index ? null : index;
};

const parseJsonObjectText = (text: string, errorMessage: string) => {
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(errorMessage);
    }
    return parsed as JsonSchemaObject;
  } catch {
    throw new Error(errorMessage);
  }
};

const getAllowedDomains = () => (
  allowedDomainsText.value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
);

const clearArgumentValues = () => {
  Object.keys(argumentValues).forEach((key) => delete argumentValues[key]);
};

const getParamFields = (toolPreview: RpaMcpPreview | null): GatewayParamField[] => {
  const schema = (toolPreview?.input_schema || {}) as { properties?: Record<string, any>; required?: string[] };
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  return Object.entries(properties)
    .filter(([key]) => key !== 'cookies')
    .map(([key, value]) => ({
      key,
      type: typeof value?.type === 'string' ? value.type : 'string',
      description: typeof value?.description === 'string' ? value.description : '',
      required: required.has(key),
      defaultValue: value?.default,
    }));
};

const getAllowedCookieDomains = () => {
  const domains = new Set<string>(getAllowedDomains());
  if (postAuthStartUrl.value) {
    try {
      const host = new URL(postAuthStartUrl.value).hostname;
      if (host) domains.add(host);
    } catch {
      // ignore invalid URL here
    }
  }
  return Array.from(domains);
};

const paramFields = computed(() => getParamFields(preview.value));
const allowedCookieDomains = computed(() => getAllowedCookieDomains());
const currentPreviewSignature = computed(() => buildPreviewDraftSignature({
  sessionId: sessionId.value,
  name: toolName.value,
  description: description.value,
  allowedDomains: getAllowedDomains(),
  postAuthStartUrl: postAuthStartUrl.value,
}));
const hasMatchingSuccessfulTest = computed(() => hasMatchingPreviewTest(currentPreviewSignature.value, lastSuccessfulTestSignature.value));
const hasConfigChangesSinceLastTest = computed(() => Boolean(lastSuccessfulTestSignature.value) && !hasMatchingSuccessfulTest.value);
const previewTestStatus = computed(() => getPreviewTestStatus({
  hasMatchingSuccessfulTest: hasMatchingSuccessfulTest.value,
  testResult: testResult.value,
  hasConfigChangesSinceLastTest: hasConfigChangesSinceLastTest.value,
}));
const previewTestStatusLabel = computed(() => {
  if (previewTestStatus.value === 'success') return 'Preview test passed';
  if (previewTestStatus.value === 'stale') return 'Preview test is out of date';
  if (previewTestStatus.value === 'failed') return 'Preview test failed';
  return 'Preview test required';
});
const previewTestStatusDescription = computed(() => {
  if (previewTestStatus.value === 'success') return 'This draft can now be saved as an MCP tool.';
  if (previewTestStatus.value === 'stale') return 'You changed the draft after testing. Run preview test again before saving.';
  if (previewTestStatus.value === 'failed') return 'Fix the current draft inputs and run preview test again before saving.';
  return 'Run a preview test on this page before saving the tool.';
});
const previewTestStatusClass = computed(() => {
  if (previewTestStatus.value === 'success') return 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200';
  if (previewTestStatus.value === 'stale') return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200';
  if (previewTestStatus.value === 'failed') return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200';
  return 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-200';
});
const cookieInputPlaceholder = computed(() => {
  if (cookieMode.value === 'cookie_header') return 'Cookie: sid=abc; theme=dark';
  if (cookieMode.value === 'header_value') return 'sid=abc; theme=dark';
  return '[{"name":"sid","value":"abc","domain":".example.com","path":"/"}]';
});
const pageTitle = computed(() => source.value === 'rpa-session' ? 'Create MCP Tool' : 'MCP Tool Editor');
const pageDescription = computed(() => source.value === 'rpa-session'
  ? 'Publish an RPA recording as a reusable MCP tool.'
  : 'Edit MCP tool metadata, schemas, and preview test state.');

const loadRecordedSession = async () => {
  if (!sessionId.value) {
    recordedSteps.value = [];
    return;
  }
  stepsLoading.value = true;
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = resp.data.session;
    recordedSteps.value = (session.steps || []) as RecordedStepItem[];
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || 'Failed to load recorded steps');
  } finally {
    stepsLoading.value = false;
  }
};

const loadPreview = async () => {
  if (!sessionId.value) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    const baseName = typeof route.query.skillName === 'string' && route.query.skillName.trim() ? route.query.skillName.trim() : 'rpa_tool';
    if (!toolName.value) toolName.value = baseName;
    if (!description.value) description.value = typeof route.query.skillDescription === 'string' ? route.query.skillDescription : '';
    preview.value = await previewRpaMcpTool(sessionId.value, {
      name: toolName.value,
      description: description.value,
      allowed_domains: getAllowedDomains(),
      post_auth_start_url: postAuthStartUrl.value,
    });
    toolName.value = preview.value.name;
    description.value = preview.value.description || description.value;
    postAuthStartUrl.value = preview.value.post_auth_start_url || postAuthStartUrl.value;
    allowedDomainsText.value = (preview.value.allowed_domains || []).join('\n');
    outputSchemaText.value = formatJsonBlock(preview.value.recommended_output_schema || preview.value.output_schema || {});
    cookieSectionOpen.value = Boolean(preview.value.requires_cookies);
    cookieDomain.value = allowedCookieDomains.value[0] || '';
    clearArgumentValues();
    for (const field of getParamFields(preview.value)) {
      if (field.defaultValue !== undefined) {
        argumentValues[field.key] = field.type === 'boolean' ? Boolean(field.defaultValue) : String(field.defaultValue);
      } else {
        argumentValues[field.key] = field.type === 'boolean' ? false : '';
      }
    }
    hasSuccessfulTest.value = Boolean(preview.value.output_examples?.length);
    lastSuccessfulTestSignature.value = hasSuccessfulTest.value ? currentPreviewSignature.value : null;
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to load MCP preview');
  } finally {
    loading.value = false;
  }
};

const buildArgumentsPayload = () => {
  const payload: Record<string, unknown> = {};
  for (const field of paramFields.value) {
    const rawValue = argumentValues[field.key];
    const isBlank = rawValue === '' || rawValue === null || rawValue === undefined;
    if (isBlank) {
      if (field.required) {
        throw new Error(`Parameter "${field.key}" is required`);
      }
      continue;
    }
    if (field.type === 'boolean') {
      payload[field.key] = Boolean(rawValue);
      continue;
    }
    if (field.type === 'number' || field.type === 'integer') {
      const numericValue = Number(rawValue);
      if (Number.isNaN(numericValue) || (field.type === 'integer' && !Number.isInteger(numericValue))) {
        throw new Error(`Parameter "${field.key}" must be a valid number`);
      }
      payload[field.key] = numericValue;
      continue;
    }
    if (field.type === 'array' || field.type === 'object') {
      try {
        payload[field.key] = typeof rawValue === 'string' ? JSON.parse(rawValue) : rawValue;
      } catch {
        throw new Error(`Parameter "${field.key}" must be valid JSON`);
      }
      continue;
    }
    payload[field.key] = String(rawValue);
  }
  return payload;
};

const runPreviewTest = async () => {
  if (!sessionId.value || !preview.value) return;
  testing.value = true;
  try {
    const argumentsPayload = buildArgumentsPayload();
    const cookies = convertCookieInputToPlaywrightCookies({
      mode: cookieMode.value,
      text: cookieText.value,
      domain: cookieMode.value === 'playwright_json' ? undefined : cookieDomain.value,
      required: Boolean(preview.value.requires_cookies),
    });
    testResult.value = await testPreviewRpaMcpTool(sessionId.value, {
      name: toolName.value,
      description: description.value,
      allowed_domains: getAllowedDomains(),
      post_auth_start_url: postAuthStartUrl.value,
      arguments: argumentsPayload,
      cookies: cookies as Array<Record<string, unknown>> | undefined,
    });
    hasSuccessfulTest.value = Boolean(testResult.value.success);
    lastSuccessfulTestSignature.value = testResult.value.success ? currentPreviewSignature.value : null;
    await loadPreview();
    showSuccessToast(testResult.value.message || 'Preview test completed');
  } catch (error: any) {
    hasSuccessfulTest.value = false;
    lastSuccessfulTestSignature.value = null;
    console.error(error);
    showErrorToast(error?.message || 'Preview test failed');
  } finally {
    testing.value = false;
  }
};

const saveTool = async () => {
  if (!sessionId.value) return;
  if (!hasMatchingSuccessfulTest.value) {
    showErrorToast('Run a successful preview test before saving this tool');
    focusPreviewTestSection(previewTestSection.value);
    return;
  }
  saving.value = true;
  try {
    await createRpaMcpTool(sessionId.value, {
      name: toolName.value,
      description: description.value,
      post_auth_start_url: postAuthStartUrl.value,
      allowed_domains: getAllowedDomains(),
      output_schema: parseJsonObjectText(outputSchemaText.value, 'Output schema JSON must be a JSON object'),
    });
    showSuccessToast('Converted tool saved');
    router.push('/chat/tools');
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to save MCP tool');
  } finally {
    saving.value = false;
  }
};

const promoteLocator = async (stepIndex: number, candidateIndex: number) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = stepIndex;
  try {
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${stepIndex}/locator`, {
      candidate_index: candidateIndex,
    });
    await Promise.all([loadRecordedSession(), loadPreview()]);
    expandedStepIndex.value = stepIndex;
    showSuccessToast('Step locator updated');
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.response?.data?.detail || error?.message || 'Failed to switch locator');
  } finally {
    promotingStepIndex.value = null;
  }
};

onMounted(async () => {
  await Promise.all([loadRecordedSession(), loadPreview()]);
});
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-[#f5f7fb] text-slate-900 dark:bg-[#101115] dark:text-slate-100">
    <div class="flex-1 overflow-y-auto">
      <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div class="mb-6 flex items-center justify-between gap-4">
        <button class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold dark:border-white/10 dark:bg-white/5" @click="router.back()">
          <ArrowLeft :size="16" />
          Back
        </button>
        <button
          class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-5 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="saving || loading || !hasMatchingSuccessfulTest"
          @click="saveTool"
        >
          <Save :size="16" />
          {{ saving ? 'Saving...' : 'Save as MCP Tool' }}
        </button>
      </div>

      <div class="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <section class="space-y-4 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <div class="flex items-center gap-3">
            <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">
              <Wand2 :size="18" />
            </div>
            <div>
              <h1 class="text-xl font-black">{{ pageTitle }}</h1>
              <p class="text-sm text-slate-500 dark:text-slate-400">{{ pageDescription }}</p>
            </div>
          </div>

          <div v-if="loading" class="rounded-2xl border border-dashed border-slate-300 p-8 text-sm text-slate-500 dark:border-white/10">Loading preview...</div>

          <template v-else-if="preview">
            <section class="rounded-3xl border p-4" :class="previewTestStatusClass">
              <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <p class="text-sm font-black">{{ previewTestStatusLabel }}</p>
                  <p class="text-sm opacity-90">{{ previewTestStatusDescription }}</p>
                </div>
                <button
                  class="inline-flex items-center gap-2 rounded-full border border-current/20 bg-white/80 px-4 py-2 text-sm font-semibold text-inherit dark:bg-[#17181d]"
                  :disabled="testing"
                  @click="runPreviewTest"
                >
                  <Beaker :size="16" />
                  {{ testing ? 'Testing...' : 'Run preview test' }}
                </button>
              </div>
            </section>

            <div class="grid gap-4 md:grid-cols-2">
              <label class="block space-y-2">
                <span class="text-sm font-semibold">Tool name</span>
                <input v-model="toolName" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" />
              </label>
              <label class="block space-y-2">
                <span class="text-sm font-semibold">Post-login start URL</span>
                <input v-model="postAuthStartUrl" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" />
              </label>
            </div>

            <label class="block space-y-2">
              <span class="text-sm font-semibold">Description</span>
              <textarea v-model="description" rows="3" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" />
            </label>

            <label class="block space-y-2">
              <span class="text-sm font-semibold">Allowed domains</span>
              <textarea v-model="allowedDomainsText" rows="4" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-sm outline-none dark:border-white/10 dark:bg-white/5" />
            </label>

            <section ref="previewTestSection" class="rounded-3xl border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="mb-4 flex items-center justify-between gap-3">
                <div class="flex items-center gap-3">
                  <div class="flex h-9 w-9 items-center justify-center rounded-2xl bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200">
                    <Beaker :size="17" />
                  </div>
                  <div>
                    <h2 class="text-base font-black">Run & Test</h2>
                    <p class="text-sm text-slate-500 dark:text-slate-400">Use the current draft config to validate the tool before saving.</p>
                  </div>
                </div>
                <button
                  data-preview-test-action
                  class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold dark:border-white/10 dark:bg-white/5"
                  :disabled="testing"
                  @click="runPreviewTest"
                >
                  <Beaker :size="16" />
                  {{ testing ? 'Testing...' : 'Run preview test' }}
                </button>
              </div>

              <div v-if="paramFields.length" class="grid gap-4 md:grid-cols-2">
                <label v-for="field in paramFields" :key="field.key" class="block space-y-2">
                  <span class="text-sm font-semibold">{{ field.key }}<template v-if="field.required"> *</template></span>
                  <select v-if="field.type === 'boolean'" v-model="argumentValues[field.key]" class="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5">
                    <option :value="true">true</option>
                    <option :value="false">false</option>
                  </select>
                  <textarea
                    v-else-if="field.type === 'array' || field.type === 'object'"
                    v-model="argumentValues[field.key]"
                    class="min-h-[120px] w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5"
                    :placeholder="field.type === 'array' ? '[]' : '{}'"
                  ></textarea>
                  <input
                    v-else
                    v-model="argumentValues[field.key]"
                    class="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5"
                    :type="field.type === 'number' || field.type === 'integer' ? 'number' : 'text'"
                    :placeholder="field.defaultValue !== undefined ? String(field.defaultValue) : field.key"
                  />
                  <p class="text-xs text-slate-500 dark:text-slate-400">{{ field.description || field.type }}</p>
                </label>
              </div>

              <div v-if="preview.requires_cookies || cookieSectionOpen" class="mt-4 space-y-4 rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.04]">
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <h3 class="text-sm font-bold">Gateway test cookies</h3>
                    <p class="text-xs text-slate-500 dark:text-slate-400">
                      {{ preview.requires_cookies ? 'This draft removed login steps, so cookies are required.' : 'Cookies are optional for this draft.' }}
                    </p>
                  </div>
                  <button v-if="!preview.requires_cookies" class="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold dark:border-white/10" @click="cookieSectionOpen = !cookieSectionOpen">
                    {{ cookieSectionOpen ? 'Hide cookie input' : 'Show cookie input' }}
                  </button>
                </div>

                <div class="inline-flex rounded-full border border-slate-200 bg-slate-100 p-1 dark:border-white/10 dark:bg-white/10">
                  <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'cookie_header' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'cookie_header'">Cookie header</button>
                  <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'header_value' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'header_value'">Header value</button>
                  <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'playwright_json' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'playwright_json'">Playwright JSON</button>
                </div>

                <label v-if="cookieMode !== 'playwright_json'" class="block space-y-2">
                  <span class="text-sm font-semibold">Cookie domain</span>
                  <input v-model="cookieDomain" list="tool-editor-cookie-domain-list" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" placeholder="example.com" />
                  <datalist id="tool-editor-cookie-domain-list">
                    <option v-for="domain in allowedCookieDomains" :key="domain" :value="domain"></option>
                  </datalist>
                </label>

                <label class="block space-y-2">
                  <span class="text-sm font-semibold">Cookie input</span>
                  <textarea v-model="cookieText" class="min-h-[140px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs outline-none dark:border-white/10 dark:bg-white/5" :placeholder="cookieInputPlaceholder"></textarea>
                  <p class="text-xs text-slate-500 dark:text-slate-400">Accepts `Cookie: a=1; b=2`, `a=1; b=2`, or Playwright cookie array JSON.</p>
                </label>
              </div>

              <div v-if="testResult" class="mt-4 rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.04]">
                <div class="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h3 class="text-sm font-bold">Latest test result</h3>
                    <p class="text-xs text-slate-500 dark:text-slate-400">{{ testResult.message || '-' }}</p>
                  </div>
                  <span class="rounded-full px-3 py-1 text-xs font-bold" :class="testResult.success ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200' : 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'">
                    {{ testResult.success ? 'Success' : 'Failed' }}
                  </span>
                </div>
                <pre class="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs dark:border-white/10 dark:bg-[#101115]"><code>{{ JSON.stringify(testResult, null, 2) }}</code></pre>
              </div>
            </section>

            <section class="rounded-3xl border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 class="text-base font-black">Recorded steps</h2>
                  <p class="text-sm text-slate-500 dark:text-slate-400">Tune locators here before the next preview test. Changes apply to the session and refresh the MCP draft.</p>
                </div>
                <div class="rounded-full bg-white px-4 py-1.5 text-xs font-bold text-violet-700 shadow-sm ring-1 ring-violet-100 dark:bg-white/[0.06] dark:text-violet-200 dark:ring-white/10">
                  {{ recordedSteps.length }} steps
                </div>
              </div>

              <div v-if="stepsLoading" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
                Loading recorded steps...
              </div>

              <div v-else-if="recordedSteps.length === 0" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
                No recorded steps available for this session.
              </div>

              <div v-else class="space-y-3">
                <article
                  v-for="(step, idx) in recordedSteps"
                  :key="step.id"
                  class="overflow-hidden rounded-3xl border bg-white shadow-sm transition-all dark:bg-white/[0.04]"
                  :class="expandedStepIndex === idx ? 'border-violet-300 shadow-lg shadow-violet-500/10 dark:border-violet-400/30' : 'border-slate-200 dark:border-white/10'"
                >
                  <div class="cursor-pointer px-4 py-4 sm:px-5" @click="toggleStep(idx)">
                    <div class="flex items-start gap-4">
                      <div
                        class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl text-xs font-extrabold"
                        :class="expandedStepIndex === idx ? 'bg-violet-600 text-white' : 'bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400'"
                      >
                        {{ String(idx + 1).padStart(2, '0') }}
                      </div>

                      <div class="min-w-0 flex-1">
                        <div class="flex flex-wrap items-center gap-2">
                          <span
                            class="rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                            :class="getActionColor(step.action)"
                          >
                            {{ getActionLabel(step.action) }}
                          </span>
                          <span
                            v-if="step.validation?.status"
                            class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                            :class="getValidationClass(step.validation.status)"
                          >
                            {{ getValidationLabel(step.validation.status) }}
                          </span>
                          <span
                            v-if="getFrameHint(step)"
                            class="rounded-full bg-violet-50 px-2.5 py-1 text-[10px] font-semibold text-violet-700 ring-1 ring-violet-100 dark:bg-violet-500/10 dark:text-violet-200 dark:ring-violet-400/20"
                          >
                            {{ getFrameHint(step) }}
                          </span>
                        </div>

                        <h3 class="mt-2 text-sm font-bold text-slate-900 dark:text-slate-100 sm:text-[15px]">
                          {{ getStepTitle(step) }}
                        </h3>

                        <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-slate-500 dark:text-slate-400">
                          <span class="min-w-0 max-w-full truncate font-mono text-slate-600 dark:text-slate-400">
                            {{ getStepLocatorSummary(step) }}
                          </span>
                          <span v-if="getValuePreview(step)">{{ getValuePreview(step) }}</span>
                          <span v-if="getCandidateSummary(step)">{{ getCandidateSummary(step) }}</span>
                        </div>
                      </div>

                      <button
                        type="button"
                        class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-400 dark:hover:bg-white/[0.08]"
                        @click.stop="toggleStep(idx)"
                      >
                        <ChevronUp v-if="expandedStepIndex === idx" :size="18" />
                        <ChevronDown v-else :size="18" />
                      </button>
                    </div>
                  </div>

                  <div
                    v-if="expandedStepIndex === idx"
                    class="border-t border-slate-100 bg-[#faf7fd] px-4 py-4 sm:px-5 dark:border-white/10 dark:bg-[#1b1622]"
                    @click.stop
                  >
                    <div class="grid gap-3 rounded-2xl bg-white p-4 ring-1 ring-violet-100 dark:bg-white/[0.04] dark:ring-violet-400/15">
                      <div class="grid gap-2 text-sm text-slate-600 dark:text-slate-400">
                        <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">Primary</span>
                          <span class="break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatLocator(step.target) }}</span>
                        </div>
                        <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">Frame</span>
                          <span class="break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatFramePath(step.frame_path) }}</span>
                        </div>
                        <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">Validation</span>
                          <div class="flex flex-wrap items-center gap-2">
                            <span
                              v-if="step.validation?.status"
                              class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                              :class="getValidationClass(step.validation.status)"
                            >
                              {{ getValidationLabel(step.validation.status) }}
                            </span>
                            <span class="text-xs text-slate-600 dark:text-slate-400">{{ step.validation?.details || 'No extra diagnostics' }}</span>
                          </div>
                        </div>
                      </div>

                      <div v-if="step.locator_candidates?.length" class="space-y-2">
                        <div class="flex items-center justify-between">
                          <p class="text-sm font-bold text-slate-900 dark:text-slate-100">Locator candidates</p>
                          <p class="text-xs text-slate-400 dark:text-slate-500">Switching here updates the next MCP preview.</p>
                        </div>

                        <div class="space-y-2">
                          <div
                            v-for="(candidate, candidateIndex) in step.locator_candidates"
                            :key="`${step.id}-${candidateIndex}`"
                            class="flex flex-col gap-2 rounded-2xl border px-3 py-3 md:flex-row md:items-start md:justify-between md:gap-4"
                            :class="candidate.selected ? 'border-violet-300 bg-violet-50/70 dark:border-violet-400/30 dark:bg-violet-500/10' : 'border-slate-200 bg-white dark:border-white/10 dark:bg-white/[0.03]'"
                          >
                            <div class="min-w-0 flex-1">
                              <div class="flex flex-wrap items-center gap-2 text-[11px]">
                                <span class="rounded-full bg-slate-100 px-2 py-0.5 font-semibold uppercase tracking-wide text-slate-600 dark:bg-white/10 dark:text-slate-400">
                                  {{ candidate.kind || 'locator' }}
                                </span>
                                <span class="text-slate-400 dark:text-slate-500">Score {{ candidate.score ?? '-' }}</span>
                                <span class="text-slate-400 dark:text-slate-500">Strict {{ candidate.strict_match_count ?? '-' }}</span>
                                <span
                                  v-if="candidate.selected"
                                  class="rounded-full bg-violet-600 px-2 py-0.5 font-semibold text-white"
                                >
                                  Current
                                </span>
                              </div>
                              <p class="mt-1 break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatLocator(candidate.locator) }}</p>
                              <p v-if="candidate.reason" class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{{ candidate.reason }}</p>
                            </div>

                            <button
                              type="button"
                              class="shrink-0 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors"
                              :class="candidate.selected ? 'cursor-default border-slate-200 text-slate-400 dark:border-white/10 dark:text-slate-500' : 'border-violet-300 text-violet-700 hover:bg-violet-50 dark:border-violet-400/30 dark:text-violet-200 dark:hover:bg-violet-500/10'"
                              :disabled="candidate.selected || promotingStepIndex === idx"
                              @click.stop="promoteLocator(idx, candidateIndex)"
                            >
                              {{ promotingStepIndex === idx ? 'Switching...' : (candidate.selected ? 'Current' : 'Use this locator') }}
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </article>
              </div>
            </section>
          </template>
          <section v-else class="rounded-3xl border border-dashed border-slate-300 bg-slate-50/70 p-8 dark:border-white/10 dark:bg-white/[0.03]">
            <h2 class="text-lg font-black">Start from an RPA recording</h2>
            <p class="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
              This editor publishes recorded browser automation as an MCP tool. Create or open an RPA recording first, then use "Publish as MCP Tool" to hydrate this editor with the recording context.
            </p>
            <div class="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="button"
                class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-4 py-2 text-sm font-bold text-white"
                @click="router.push(buildRpaRecorderLocation())"
              >
                <Wand2 :size="16" />
                Open RPA Recorder
              </button>
              <p class="text-xs text-slate-500 dark:text-slate-400">
                Tools is the management surface; recording remains the authoring flow.
              </p>
            </div>
          </section>
        </section>

        <aside class="space-y-4">
          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200">
                <Shield :size="18" />
              </div>
              <div>
                <h2 class="text-base font-black">Sanitize report</h2>
                <p class="text-sm text-slate-500 dark:text-slate-400">Login actions are removed before the tool is shared.</p>
              </div>
            </div>
            <div v-if="preview" class="mt-4 space-y-3 text-sm">
              <div>
                <p class="font-semibold">Removed login steps</p>
                <p class="text-slate-500 dark:text-slate-400">{{ preview.sanitize_report.removed_steps.join(', ') || 'None' }}</p>
              </div>
              <div>
                <p class="font-semibold">Removed params</p>
                <p class="text-slate-500 dark:text-slate-400">{{ preview.sanitize_report.removed_params.join(', ') || 'None' }}</p>
              </div>
              <div>
                <p class="font-semibold">Warnings</p>
                <ul class="list-disc pl-5 text-slate-500 dark:text-slate-400">
                  <li v-for="warning in preview.sanitize_report.warnings" :key="warning">{{ warning }}</li>
                  <li v-if="preview.sanitize_report.warnings.length === 0">None</li>
                </ul>
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <h2 class="text-base font-black">API & Schemas</h2>
            <div v-if="preview" class="mt-4 space-y-4">
              <div>
                <p class="mb-2 text-sm font-semibold">Input Schema</p>
                <pre class="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs dark:border-white/10 dark:bg-[#101115]"><code>{{ JSON.stringify(preview.input_schema || {}, null, 2) }}</code></pre>
              </div>
              <div>
                <p class="mb-2 text-sm font-semibold">Output Schema</p>
                <textarea v-model="outputSchemaText" class="min-h-[260px] w-full rounded-2xl border border-slate-200 bg-slate-50 p-3 font-mono text-xs outline-none dark:border-white/10 dark:bg-[#101115]" spellcheck="false"></textarea>
              </div>
            </div>
          </section>

        </aside>
      </div>
    </div>
    </div>
  </div>
</template>

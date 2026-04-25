<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, Beaker, ChevronDown, ChevronUp, Save, Shield, Wand2 } from 'lucide-vue-next';

import {
  createRpaMcpTool,
  getRpaMcpExecutionPlan,
  getRpaMcpTool,
  previewRpaMcpTool,
  testPreviewRpaMcpTool,
  testRpaMcpTool,
  updateRpaMcpTool,
  type RpaMcpExecutionPlan,
  type JsonSchemaObject,
  type RpaMcpExecutionResult,
  type RpaMcpPreview,
} from '@/api/rpaMcp';
import { apiClient } from '@/api/client';
import ExecutionScriptPanel from '@/components/resourceDetail/ExecutionScriptPanel.vue';
import {
  buildRpaRecorderLocation,
  buildPreviewDraftSignature,
  focusPreviewTestSection,
  getPreviewTestStatus,
  hasMatchingPreviewTest,
} from '@/utils/rpaMcpConvert';
import { mapRpaConfigureDisplaySteps } from '@/utils/rpaConfigureTimeline';
import { buildRecordedStepSummary, buildSchemaSummary, shouldShowCookieSection } from '@/utils/rpaMcpEditorView';
import { convertCookieInputToPlaywrightCookies, type CookieInputMode } from '@/utils/rpaMcpTest';
import { showErrorToast, showSuccessToast } from '@/utils/toast';

type GatewayParamField = {
  key: string;
  type: string;
  description: string;
  required: boolean;
  defaultValue?: unknown;
};

type EditableParam = {
  id: string;
  sourceKey: string;
  name: string;
  description: string;
  type: string;
  required: boolean;
  enabled: boolean;
  defaultValue: string;
  originalValue?: unknown;
  sourceStepIndex?: number;
};

const PARAM_TYPES = ['string', 'number', 'integer', 'boolean', 'array', 'object'];

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
  source?: string;
  configurable?: boolean;
}

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const preview = ref<RpaMcpPreview | null>(null);
const testResult = ref<RpaMcpExecutionResult | null>(null);
const hasSuccessfulTest = ref(false);
const lastSuccessfulTestSignature = ref<string | null>(null);
const recordedSteps = ref<RecordedStepItem[]>([]);
const recordedStepsMode = ref<'source-session' | 'tool-steps'>('tool-steps');
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
type ArgumentValue = string;
const argumentValues = reactive<Record<string, ArgumentValue>>({});
const editableParams = ref<EditableParam[]>([]);
const source = computed(() => typeof route.query.source === 'string' ? route.query.source : '');
const savedToolId = computed(() => typeof route.params.toolId === 'string' ? route.params.toolId : '');
const editorMode = computed(() => typeof route.query.mode === 'string' ? route.query.mode : 'edit');
const isExistingTool = computed(() => Boolean(savedToolId.value));
const isViewMode = computed(() => isExistingTool.value && editorMode.value === 'view');
const viewActiveTab = ref<'overview' | 'files'>('overview');
const executionPlan = ref<RpaMcpExecutionPlan | null>(null);
const executionPlanLoading = ref(false);
const executionPlanError = ref<string | null>(null);
const canTuneRecordedSteps = computed(() => Boolean(sessionId.value) && !isExistingTool.value && !isViewMode.value);
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
  if (!locator) return t('MCP Editor No locator');
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
  if (!framePath?.length) return t('MCP Editor Main frame');
  return framePath.join(' -> ');
};

const VALIDATION_LABELS: Record<string, string> = {
  ok: 'MCP Editor Strict match',
  ambiguous: 'MCP Editor Ambiguous / not unique',
  fallback: 'MCP Editor Fallback',
  warning: 'MCP Editor Warning',
  broken: 'MCP Editor Broken',
};

const VALIDATION_CLASS_MAP: Record<string, string> = {
  ok: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400 ring-1 ring-emerald-200 dark:ring-emerald-800',
  ambiguous: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  fallback: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  warning: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  broken: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400 ring-1 ring-rose-200 dark:ring-rose-800',
};

const getValidationLabel = (status?: string) => {
  if (!status) return t('MCP Editor Unknown');
  return VALIDATION_LABELS[status] ? t(VALIDATION_LABELS[status]) : status.replace(/_/g, ' ');
};

const getValidationClass = (status?: string) => {
  if (!status) return 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
  return VALIDATION_CLASS_MAP[status] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
};

const getActionLabel = (action: string) => {
  const map: Record<string, string> = {
    click: 'MCP Editor Click',
    fill: 'MCP Editor Fill',
    press: 'MCP Editor Press',
    select: 'MCP Editor Select',
    navigate: 'MCP Editor Navigate',
    goto: 'MCP Editor Navigate',
    navigate_click: 'MCP Editor Navigate after click',
    navigate_press: 'MCP Editor Navigate after keypress',
    open_tab_click: 'MCP Editor Open tab',
    switch_tab: 'MCP Editor Switch tab',
    close_tab: 'MCP Editor Close tab',
    download_click: 'MCP Editor Download',
    download: 'MCP Editor Download',
  };
  return map[action] ? t(map[action]) : action;
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
  return shortenText(t('MCP Editor Value preview', { value: display }), 28);
};

const getFrameHint = (step: RecordedStepItem) => {
  if (!step.frame_path?.length) return '';
  return step.frame_path.length === 1
    ? t('MCP Editor iframe level', { count: step.frame_path.length })
    : t('MCP Editor iframe levels', { count: step.frame_path.length });
};

const getSelectedCandidate = (step: RecordedStepItem): LocatorCandidate | null => {
  const candidates = step.locator_candidates || [];
  return candidates.find((candidate) => candidate.selected) || candidates[0] || null;
};

const formatCandidateMatchText = (candidate: LocatorCandidate): string => {
  const strictCount = candidate.strict_match_count;
  const visibleCount = candidate.visible_match_count;

  if (typeof strictCount === 'number' && strictCount > 0) {
    return strictCount === 1
      ? t('MCP Editor strict match lower')
      : t('MCP Editor strict matches', { count: strictCount });
  }
  if (typeof visibleCount === 'number') {
    return visibleCount === 1
      ? t('MCP Editor visible match', { count: visibleCount })
      : t('MCP Editor visible matches', { count: visibleCount });
  }
  if (typeof strictCount === 'number') {
    return strictCount === 1
      ? t('MCP Editor strict match with count', { count: strictCount })
      : t('MCP Editor strict matches', { count: strictCount });
  }
  return '';
};

const getCandidateSummary = (step: RecordedStepItem) => {
  const candidates = step.locator_candidates || [];
  const total = candidates.length;
  if (!total) return '';
  const selected = getSelectedCandidate(step);
  if (!selected) return total === 1
    ? t('MCP Editor candidate count', { count: total })
    : t('MCP Editor candidates count', { count: total });

  const summary: string[] = [];
  if (selected.kind) summary.push(t('MCP Editor Current locator kind', { kind: selected.kind }));
  const matchText = formatCandidateMatchText(selected);
  if (matchText) summary.push(matchText);
  summary.push(total === 1
    ? t('MCP Editor candidate count', { count: total })
    : t('MCP Editor candidates count', { count: total }));
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

const stringifyEditorValue = (value: unknown): string => {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
};

const normalizeParamName = (value: string, fallback: string): string => {
  const normalized = value
    .trim()
    .replace(/\s+/g, '_')
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  const candidate = normalized || fallback || 'param';
  return /^[A-Za-z_]/.test(candidate) ? candidate : `param_${candidate}`;
};

const parseParamDefaultValue = (type: string, value: string): unknown => {
  const trimmed = value.trim();
  if (trimmed === '') return undefined;
  if (type === 'boolean') return trimmed === 'true';
  if (type === 'number' || type === 'integer') {
    const numericValue = Number(trimmed);
    return Number.isNaN(numericValue) ? undefined : numericValue;
  }
  if (type === 'array' || type === 'object') {
    try {
      return JSON.parse(trimmed);
    } catch {
      return undefined;
    }
  }
  return value;
};

const getParamFieldsFromSchema = (inputSchema: JsonSchemaObject | null | undefined): GatewayParamField[] => {
  const schema = (inputSchema || {}) as { properties?: Record<string, any>; required?: string[] };
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

const getSourceParamInfo = (toolPreview: RpaMcpPreview, schemaKey: string) => {
  const params = toolPreview.params || {};
  if (params[schemaKey] && typeof params[schemaKey] === 'object') {
    return params[schemaKey] as Record<string, any>;
  }
  const match = Object.values(params).find((value) => (
    value && typeof value === 'object' && (value as Record<string, any>).source_param === schemaKey
  ));
  return (match && typeof match === 'object' ? match : {}) as Record<string, any>;
};

const hydrateEditableParams = (toolPreview: RpaMcpPreview | null, options: { preserveUserEdits?: boolean } = {}) => {
  if (!toolPreview) {
    editableParams.value = [];
    return;
  }
  if (options.preserveUserEdits && editableParams.value.length) return;

  const schema = (toolPreview.input_schema || {}) as JsonSchemaObject;
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  editableParams.value = Object.entries(properties)
    .filter(([key]) => key !== 'cookies')
    .map(([key, prop], index) => {
      const info = getSourceParamInfo(toolPreview, key);
      const schemaProp = (prop || {}) as Record<string, any>;
      const originalValue = info.original_value ?? schemaProp.default;
      const sourceStepIndex = typeof info.source_step_index === 'number' ? info.source_step_index : undefined;
      return {
        id: `param-${index}-${key}`,
        sourceKey: String(info.source_param || key),
        name: key,
        description: String(schemaProp.description || info.description || ''),
        type: typeof schemaProp.type === 'string' && PARAM_TYPES.includes(schemaProp.type) ? schemaProp.type : String(info.type || 'string'),
        required: required.has(key) || Boolean(info.required),
        enabled: true,
        defaultValue: stringifyEditorValue(originalValue),
        originalValue,
        sourceStepIndex,
      };
    });
};

const buildConfirmedParams = () => {
  const confirmed: Record<string, Record<string, unknown>> = {};
  const usedNames = new Set<string>();
  for (const param of editableParams.value) {
    if (!param.enabled) continue;
    let name = normalizeParamName(param.name, param.sourceKey);
    if (usedNames.has(name)) {
      let suffix = 2;
      while (usedNames.has(`${name}_${suffix}`)) suffix += 1;
      name = `${name}_${suffix}`;
    }
    usedNames.add(name);
    const parsedDefault = parseParamDefaultValue(param.type, param.defaultValue);
    confirmed[name] = {
      original_value: parsedDefault !== undefined ? parsedDefault : param.originalValue,
      type: param.type,
      description: param.description,
      required: param.required,
      sensitive: false,
      source_param: param.sourceKey,
    };
    if (param.sourceStepIndex !== undefined) {
      confirmed[name].source_step_index = param.sourceStepIndex;
    }
  }
  return confirmed;
};

const buildConfirmedInputSchema = (): JsonSchemaObject => {
  const properties: Record<string, JsonSchemaObject> = {};
  const required: string[] = [];
  const baseSchema = (preview.value?.input_schema || {}) as JsonSchemaObject;
  const baseProperties = baseSchema.properties && typeof baseSchema.properties === 'object' ? baseSchema.properties : {};
  if (baseProperties.cookies) {
    properties.cookies = baseProperties.cookies;
    if (Array.isArray(baseSchema.required) && baseSchema.required.includes('cookies')) {
      required.push('cookies');
    }
  }

  const confirmedParams = buildConfirmedParams();
  for (const [name, info] of Object.entries(confirmedParams)) {
    const prop: JsonSchemaObject = {
      type: typeof info.type === 'string' ? info.type : 'string',
      description: typeof info.description === 'string' ? info.description : '',
    };
    const original = info.original_value;
    if (original !== undefined && original !== null && original !== '') {
      prop.default = original;
    }
    properties[name] = prop;
    if (info.required) required.push(name);
  }
  return { type: 'object', properties, required };
};

const confirmedSchemaSource = () => (editableParams.value.length ? 'user_edited' : (preview.value?.schema_source || preview.value?.semantic_inference?.source || 'rule_inferred'));

const setArgumentDefaults = (fields: GatewayParamField[]) => {
  clearArgumentValues();
  for (const field of fields) {
    if (field.defaultValue !== undefined) {
      argumentValues[field.key] = field.type === 'boolean' ? String(Boolean(field.defaultValue)) : stringifyEditorValue(field.defaultValue);
    } else {
      argumentValues[field.key] = field.type === 'boolean' ? 'false' : '';
    }
  }
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

const confirmedInputSchema = computed(() => buildConfirmedInputSchema());
const paramFields = computed(() => getParamFieldsFromSchema(confirmedInputSchema.value));
const allowedCookieDomains = computed(() => getAllowedCookieDomains());
const recordedStepSummary = computed(() => buildRecordedStepSummary(recordedSteps.value as unknown as Array<Record<string, any>>));
const schemaSummary = computed(() => buildSchemaSummary({
  input_schema: confirmedInputSchema.value,
  output_schema: preview.value?.output_schema,
}));
const schemaSourceLabel = computed(() => {
  const source = confirmedSchemaSource();
  if (source === 'ai_inferred') return t('MCP Editor AI inferred');
  if (source === 'user_edited') return t('MCP Editor User edited');
  return t('MCP Editor Rule inferred');
});
const semanticWarnings = computed(() => preview.value?.semantic_inference?.warnings || []);
const removedStepIndexSet = computed(() => new Set(preview.value?.sanitize_report?.removed_steps || []));
const canHighlightRemovedSteps = computed(() => recordedStepsMode.value === 'source-session');
const removedStepDetails = computed(() => {
  const details = preview.value?.sanitize_report?.removed_step_details || [];
  if (details.length) return details;
  return (preview.value?.sanitize_report?.removed_steps || []).map((index) => {
    const step = recordedSteps.value[index];
    return {
      index,
      action: step?.action || '',
      description: step ? getStepTitle(step) : '',
      url: step?.url || '',
    };
  });
});
const showCookieSection = computed(() => shouldShowCookieSection(preview.value, cookieSectionOpen.value));
const currentPreviewSignature = computed(() => buildPreviewDraftSignature({
  sessionId: sessionId.value,
  name: toolName.value,
  description: description.value,
  allowedDomains: getAllowedDomains(),
  postAuthStartUrl: postAuthStartUrl.value,
  inputSchema: confirmedInputSchema.value,
}));
const hasMatchingSuccessfulTest = computed(() => hasMatchingPreviewTest(currentPreviewSignature.value, lastSuccessfulTestSignature.value));
const hasConfigChangesSinceLastTest = computed(() => Boolean(lastSuccessfulTestSignature.value) && !hasMatchingSuccessfulTest.value);
const previewTestStatus = computed(() => getPreviewTestStatus({
  hasMatchingSuccessfulTest: hasMatchingSuccessfulTest.value,
  testResult: testResult.value,
  hasConfigChangesSinceLastTest: hasConfigChangesSinceLastTest.value,
}));
const previewTestStatusLabel = computed(() => {
  if (isExistingTool.value && !testResult.value) return t('MCP Editor Saved MCP tool');
  if (previewTestStatus.value === 'success') return t('MCP Editor Preview test passed');
  if (previewTestStatus.value === 'stale') return t('MCP Editor Preview test is out of date');
  if (previewTestStatus.value === 'failed') return t('MCP Editor Preview test failed');
  return t('MCP Editor Preview test required');
});
const previewTestStatusDescription = computed(() => {
  if (isExistingTool.value && !testResult.value) return t('MCP Editor Existing tools can be viewed, edited, and tested from this page.');
  if (previewTestStatus.value === 'success') return t('MCP Editor This draft can now be saved as an MCP tool.');
  if (previewTestStatus.value === 'stale') return t('MCP Editor You changed the draft after testing. Run preview test again before saving.');
  if (previewTestStatus.value === 'failed') return t('MCP Editor Fix the current draft inputs and run preview test again before saving.');
  return t('MCP Editor Run a preview test on this page before saving the tool.');
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
const pageTitle = computed(() => {
  if (isViewMode.value) return t('MCP Editor View MCP Tool');
  if (isExistingTool.value) return t('MCP Editor Edit MCP Tool');
  return source.value === 'rpa-session' ? t('MCP Editor Create MCP Tool') : t('MCP Editor MCP Tool Editor');
});
const pageDescription = computed(() => {
  if (isViewMode.value) return t('MCP Editor View saved MCP tool metadata, schemas, test state, and recorded steps.');
  if (isExistingTool.value) return t('MCP Editor Edit saved MCP tool metadata, schemas, test state, and recorded steps.');
  return source.value === 'rpa-session'
    ? t('MCP Editor Publish an RPA recording as a reusable MCP tool.')
    : t('MCP Editor Edit MCP tool metadata, schemas, preview test state, and recorded steps.');
});

const loadExecutionPlan = async () => {
  if (!savedToolId.value || executionPlan.value || executionPlanLoading.value) return;
  executionPlanLoading.value = true;
  executionPlanError.value = null;
  try {
    executionPlan.value = await getRpaMcpExecutionPlan(savedToolId.value);
  } catch (error: any) {
    executionPlanError.value = error?.message || t('Failed to load file content');
  } finally {
    executionPlanLoading.value = false;
  }
};

const isRemovedStep = (index: number) => canHighlightRemovedSteps.value && removedStepIndexSet.value.has(index);
const canPromoteStepLocator = (step: RecordedStepItem) => (
  canTuneRecordedSteps.value
  && step.configurable !== false
  && step.source !== 'ai'
);

const loadRecordedSession = async (sourceSessionId?: string, options: { silent?: boolean } = {}) => {
  const targetSessionId = sourceSessionId || sessionId.value;
  if (!targetSessionId) {
    recordedSteps.value = preview.value?.steps ? (preview.value.steps as unknown as RecordedStepItem[]) : [];
    recordedStepsMode.value = 'tool-steps';
    return;
  }
  stepsLoading.value = true;
  try {
    const resp = await apiClient.get(`/rpa/session/${targetSessionId}`);
    const session = resp.data.session;
    recordedSteps.value = mapRpaConfigureDisplaySteps(session) as RecordedStepItem[];
    recordedStepsMode.value = 'source-session';
  } catch (error: any) {
    console.error(error);
    recordedSteps.value = preview.value?.steps ? (preview.value.steps as unknown as RecordedStepItem[]) : [];
    recordedStepsMode.value = 'tool-steps';
    if (!options.silent) {
      showErrorToast(error?.message || t('MCP Editor Failed to load recorded steps'));
    }
  } finally {
    stepsLoading.value = false;
  }
};

const loadPreview = async (options: { preserveUserParamEdits?: boolean } = {}) => {
  if (!sessionId.value) {
    loading.value = false;
    return;
  }
  loading.value = true;
  const previousSuccessfulSignature = lastSuccessfulTestSignature.value;
  const previousHasSuccessfulTest = hasSuccessfulTest.value;
  try {
    const baseName = typeof route.query.skillName === 'string' && route.query.skillName.trim() ? route.query.skillName.trim() : 'rpa_tool';
    if (!toolName.value) toolName.value = baseName;
    if (!description.value) description.value = typeof route.query.skillDescription === 'string' ? route.query.skillDescription : '';
    const confirmedContract = options.preserveUserParamEdits && editableParams.value.length
      ? {
          input_schema: confirmedInputSchema.value,
          params: buildConfirmedParams(),
          schema_source: confirmedSchemaSource(),
        }
      : {};
    preview.value = await previewRpaMcpTool(sessionId.value, {
      name: toolName.value,
      description: description.value,
      allowed_domains: getAllowedDomains(),
      post_auth_start_url: postAuthStartUrl.value,
      ...confirmedContract,
    });
    toolName.value = preview.value.name;
    description.value = preview.value.description || description.value;
    postAuthStartUrl.value = preview.value.post_auth_start_url || postAuthStartUrl.value;
    allowedDomainsText.value = (preview.value.allowed_domains || []).join('\n');
    outputSchemaText.value = formatJsonBlock(preview.value.recommended_output_schema || preview.value.output_schema || {});
    cookieSectionOpen.value = Boolean(preview.value.requires_cookies);
    cookieDomain.value = allowedCookieDomains.value[0] || '';
    hydrateEditableParams(preview.value, { preserveUserEdits: options.preserveUserParamEdits });
    if (!options.preserveUserParamEdits) {
      setArgumentDefaults(paramFields.value);
    }
    const hasPersistedPreviewResult = Boolean(preview.value.output_examples?.length);
    if (hasPersistedPreviewResult) {
      hasSuccessfulTest.value = true;
      lastSuccessfulTestSignature.value = currentPreviewSignature.value;
    } else if (options.preserveUserParamEdits) {
      hasSuccessfulTest.value = previousHasSuccessfulTest;
      lastSuccessfulTestSignature.value = previousSuccessfulSignature;
    } else {
      hasSuccessfulTest.value = false;
      lastSuccessfulTestSignature.value = null;
    }
  } catch (error: any) {
    showErrorToast(error?.message || t('MCP Editor Failed to load MCP preview'));
  } finally {
    loading.value = false;
  }
};

const applyToolToEditor = (tool: RpaMcpPreview) => {
  preview.value = tool;
  toolName.value = tool.name;
  description.value = tool.description || '';
  postAuthStartUrl.value = tool.post_auth_start_url || '';
  allowedDomainsText.value = (tool.allowed_domains || []).join('\n');
  outputSchemaText.value = formatJsonBlock(tool.output_schema || tool.recommended_output_schema || {});
  recordedSteps.value = (tool.steps || []) as unknown as RecordedStepItem[];
  recordedStepsMode.value = 'tool-steps';
  cookieSectionOpen.value = Boolean(tool.requires_cookies);
  cookieDomain.value = allowedCookieDomains.value[0] || '';
  hydrateEditableParams(tool);
  setArgumentDefaults(paramFields.value);
  hasSuccessfulTest.value = true;
  lastSuccessfulTestSignature.value = currentPreviewSignature.value;
};

const loadExistingTool = async () => {
  if (!savedToolId.value) return;
  loading.value = true;
  stepsLoading.value = true;
  try {
    const tool = await getRpaMcpTool(savedToolId.value);
    applyToolToEditor(tool);
    const sourceSessionId = typeof tool.source?.session_id === 'string' ? tool.source.session_id : '';
    if (sourceSessionId) {
      await loadRecordedSession(sourceSessionId, { silent: true });
    } else {
      stepsLoading.value = false;
    }
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('MCP Editor Failed to load MCP tool'));
  } finally {
    loading.value = false;
    if (recordedStepsMode.value !== 'source-session') {
      stepsLoading.value = false;
    }
  }
};

const buildArgumentsPayload = () => {
  const payload: Record<string, unknown> = {};
  for (const field of paramFields.value) {
    const rawValue = argumentValues[field.key] ?? (field.defaultValue !== undefined ? stringifyEditorValue(field.defaultValue) : '');
    const isBlank = rawValue === '' || rawValue === null || rawValue === undefined;
    if (isBlank) {
      if (field.required) {
        throw new Error(t('Gateway parameter required', { name: field.key }));
      }
      continue;
    }
    if (field.type === 'boolean') {
      payload[field.key] = rawValue === 'true';
      continue;
    }
    if (field.type === 'number' || field.type === 'integer') {
      const numericValue = Number(rawValue);
      if (Number.isNaN(numericValue) || (field.type === 'integer' && !Number.isInteger(numericValue))) {
        throw new Error(t('Gateway parameter number invalid', { name: field.key }));
      }
      payload[field.key] = numericValue;
      continue;
    }
    if (field.type === 'array' || field.type === 'object') {
      try {
        payload[field.key] = typeof rawValue === 'string' ? JSON.parse(rawValue) : rawValue;
      } catch {
        throw new Error(t('Gateway parameter JSON invalid', { name: field.key }));
      }
      continue;
    }
    payload[field.key] = String(rawValue);
  }
  return payload;
};

const runPreviewTest = async () => {
  if (!preview.value) return;
  testing.value = true;
  try {
    const argumentsPayload = buildArgumentsPayload();
    const cookies = convertCookieInputToPlaywrightCookies({
      mode: cookieMode.value,
      text: cookieText.value,
      domain: cookieMode.value === 'playwright_json' ? undefined : cookieDomain.value,
      required: Boolean(preview.value.requires_cookies),
    });
    if (isExistingTool.value) {
      testResult.value = await testRpaMcpTool(savedToolId.value, {
        arguments: argumentsPayload,
        cookies: cookies as Array<Record<string, unknown>> | undefined,
      });
    } else {
      if (!sessionId.value) return;
      testResult.value = await testPreviewRpaMcpTool(sessionId.value, {
        name: toolName.value,
        description: description.value,
        allowed_domains: getAllowedDomains(),
        post_auth_start_url: postAuthStartUrl.value,
        input_schema: confirmedInputSchema.value,
        params: buildConfirmedParams(),
        schema_source: confirmedSchemaSource(),
        arguments: argumentsPayload,
        cookies: cookies as Array<Record<string, unknown>> | undefined,
      });
    }
    hasSuccessfulTest.value = Boolean(testResult.value.success);
    lastSuccessfulTestSignature.value = testResult.value.success ? currentPreviewSignature.value : null;
    if (!isExistingTool.value) {
      await loadPreview({ preserveUserParamEdits: true });
    }
    showSuccessToast(testResult.value.message || t('MCP Editor Preview test completed'));
  } catch (error: any) {
    hasSuccessfulTest.value = false;
    lastSuccessfulTestSignature.value = null;
    console.error(error);
    showErrorToast(error?.message || t('MCP Editor Preview test failed'));
  } finally {
    testing.value = false;
  }
};

const saveTool = async () => {
  if (isViewMode.value) return;
  if (!isExistingTool.value && !sessionId.value) return;
  if (!isExistingTool.value && !hasMatchingSuccessfulTest.value) {
    showErrorToast(t('MCP Editor Run a successful preview test before saving this tool'));
    focusPreviewTestSection(previewTestSection.value);
    return;
  }
  saving.value = true;
  try {
    const payload = {
      name: toolName.value,
      description: description.value,
      post_auth_start_url: postAuthStartUrl.value,
      allowed_domains: getAllowedDomains(),
      input_schema: confirmedInputSchema.value,
      params: buildConfirmedParams(),
      schema_source: confirmedSchemaSource(),
      output_schema: parseJsonObjectText(outputSchemaText.value, t('Output schema JSON invalid')),
      output_schema_confirmed: true,
    };
    if (isExistingTool.value) {
      const updated = await updateRpaMcpTool(savedToolId.value, {
        ...payload,
        enabled: preview.value?.enabled ?? true,
      });
      applyToolToEditor(updated);
      showSuccessToast(t('MCP Editor Tool updated'));
    } else {
      await createRpaMcpTool(sessionId.value, payload);
      showSuccessToast(t('MCP Editor Converted tool saved'));
    }
    router.push('/chat/tools');
  } catch (error: any) {
    showErrorToast(error?.message || t('MCP Editor Failed to save MCP tool'));
  } finally {
    saving.value = false;
  }
};

const promoteLocator = async (stepIndex: number, candidateIndex: number) => {
  if (!canTuneRecordedSteps.value || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = stepIndex;
  try {
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${stepIndex}/locator`, {
      candidate_index: candidateIndex,
    });
    await Promise.all([loadRecordedSession(), loadPreview()]);
    expandedStepIndex.value = stepIndex;
    showSuccessToast(t('MCP Editor Step locator updated'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.response?.data?.detail || error?.message || t('MCP Editor Failed to switch locator'));
  } finally {
    promotingStepIndex.value = null;
  }
};

onMounted(async () => {
  if (isExistingTool.value) {
    await loadExistingTool();
    return;
  }
  await Promise.all([loadRecordedSession(), loadPreview()]);
});

watch(viewActiveTab, async (tab) => {
  if (isViewMode.value && tab === 'files') {
    await loadExecutionPlan();
  }
});
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-[#f5f7fb] text-slate-900 dark:bg-[#101115] dark:text-slate-100">
    <div class="flex-1 overflow-y-auto">
      <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div class="mb-6 flex items-center justify-between gap-4">
        <button class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold dark:border-white/10 dark:bg-white/5" @click="router.back()">
          <ArrowLeft :size="16" />
          {{ t('Back') }}
        </button>
        <button
          v-if="isViewMode"
          class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-5 py-2 text-sm font-bold text-white"
          @click="router.push({ path: `/chat/tools/mcp/${savedToolId}`, query: { mode: 'edit' } })"
        >
          <Save :size="16" />
          {{ t('Edit') }}
        </button>
        <button
          v-else
          class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-5 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="saving || loading || (!isExistingTool && !hasMatchingSuccessfulTest)"
          @click="saveTool"
        >
          <Save :size="16" />
          {{ saving ? t('Saving...') : (isExistingTool ? t('Save changes') : t('MCP Editor Save as MCP Tool')) }}
        </button>
      </div>

      <div v-if="isViewMode" class="space-y-6">
        <div class="inline-flex rounded-full border border-slate-200 bg-white p-1 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <button
            class="rounded-full px-4 py-2 text-sm font-semibold transition-colors"
            :class="viewActiveTab === 'overview' ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900' : 'text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'"
            @click="viewActiveTab = 'overview'"
          >
            {{ t('Overview') }}
          </button>
          <button
            class="rounded-full px-4 py-2 text-sm font-semibold transition-colors"
            :class="viewActiveTab === 'files' ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900' : 'text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'"
            @click="viewActiveTab = 'files'"
          >
            {{ t('Files') }}
          </button>
        </div>
        <div v-if="loading" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-8 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
          {{ t('MCP Editor Loading preview...') }}
        </div>

        <template v-else-if="viewActiveTab === 'files' && preview">
          <div class="space-y-6 overflow-y-auto bg-[#f5f7fb] py-2 dark:bg-transparent">
            <ExecutionScriptPanel
              :script="executionPlan?.compiled_script || ''"
              :loading="executionPlanLoading"
              :error="executionPlanError"
              :title="t('Current execution script')"
              :description="t(`Read-only generated script for the tool's current execution plan.`)"
              :generated-at="executionPlan?.generated_at || ''"
            />

            <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
              <h2 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ t('MCP Editor Input Schema') }}</h2>
              <pre class="mt-4 overflow-x-auto rounded-2xl bg-[#101115] p-4 text-xs text-slate-100"><code>{{ JSON.stringify(confirmedInputSchema, null, 2) }}</code></pre>
            </section>

            <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
              <h2 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ t('MCP Editor Output Schema') }}</h2>
              <pre class="mt-4 overflow-x-auto rounded-2xl bg-[#101115] p-4 text-xs text-slate-100"><code>{{ JSON.stringify(preview.output_schema || {}, null, 2) }}</code></pre>
            </section>
          </div>
        </template>

        <section v-else-if="viewActiveTab === 'files'" class="rounded-lg border border-dashed border-slate-300 bg-slate-50/70 p-8 dark:border-white/10 dark:bg-white/[0.03]">
          <h2 class="text-lg font-black">{{ t('MCP Editor Start from an RPA recording') }}</h2>
          <p class="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
            {{ t('MCP Editor Empty state hint') }}
          </p>
        </section>
      </div>

      <div v-if="!isViewMode || viewActiveTab === 'overview'" class="grid gap-6 xl:grid-cols-[minmax(0,1.02fr)_minmax(360px,0.98fr)]">
        <section class="space-y-5 rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <div class="flex items-center gap-3">
            <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">
              <Wand2 :size="18" />
            </div>
            <div>
              <h1 class="text-xl font-black">{{ pageTitle }}</h1>
              <p class="text-sm text-slate-500 dark:text-slate-400">{{ pageDescription }}</p>
            </div>
          </div>

          <div v-if="loading" class="rounded-2xl border border-dashed border-slate-300 p-8 text-sm text-slate-500 dark:border-white/10">{{ t('MCP Editor Loading preview...') }}</div>

          <template v-else-if="preview">
            <section class="rounded-lg border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="mb-4">
                <h2 class="text-base font-black">{{ t('MCP Editor Basic Info') }}</h2>
                <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Basic info hint') }}</p>
              </div>
              <div class="grid gap-4 md:grid-cols-2">
                <label class="block space-y-2">
                  <span class="text-sm font-semibold">{{ t('MCP Editor Tool name') }}</span>
                  <input v-model="toolName" :disabled="isViewMode" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
                </label>
                <label class="block space-y-2">
                  <span class="text-sm font-semibold">{{ t('MCP Editor Post-login start URL') }}</span>
                  <input v-model="postAuthStartUrl" :disabled="isViewMode" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
                </label>
              </div>

              <label class="mt-4 block space-y-2">
                <span class="text-sm font-semibold">{{ t('MCP Editor Description') }}</span>
                <textarea v-model="description" :disabled="isViewMode" rows="3" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
              </label>

              <label class="mt-4 block space-y-2">
                <span class="text-sm font-semibold">{{ t('MCP Editor Allowed domains') }}</span>
                <textarea v-model="allowedDomainsText" :disabled="isViewMode" rows="4" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
                <span class="block text-xs text-slate-500 dark:text-slate-400">{{ t('MCP Editor Allowed domains hint') }}</span>
              </label>
            </section>

            <section class="rounded-lg border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 class="text-base font-black">{{ t('MCP Editor Input parameters') }}</h2>
                  <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Input parameters hint') }}</p>
                </div>
                <span class="rounded-full bg-white px-4 py-1.5 text-xs font-bold text-violet-700 shadow-sm ring-1 ring-violet-100 dark:bg-white/[0.06] dark:text-violet-200 dark:ring-white/10">
                  {{ editableParams.filter((param) => param.enabled).length }} / {{ editableParams.length }}
                </span>
              </div>

              <div v-if="editableParams.length === 0" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
                {{ t('MCP Editor No input parameters') }}
              </div>

              <div v-else class="space-y-3">
                <div
                  v-for="param in editableParams"
                  :key="param.id"
                  class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.04]"
                  :class="param.enabled ? '' : 'opacity-60'"
                >
                  <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <label class="inline-flex items-center gap-2 text-sm font-semibold">
                      <input v-model="param.enabled" :disabled="isViewMode" type="checkbox" class="h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500 disabled:cursor-not-allowed" />
                      {{ t('MCP Editor Parameter enabled') }}
                    </label>
                    <span class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-500 dark:bg-white/10 dark:text-slate-400">
                      {{ t('MCP Editor Source parameter') }} {{ param.sourceKey }}<template v-if="param.sourceStepIndex !== undefined"> · {{ t('MCP Editor Step number', { number: param.sourceStepIndex + 1 }) }}</template>
                    </span>
                  </div>

                  <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_150px_120px]">
                    <label class="block space-y-1.5">
                      <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Parameter name') }}</span>
                      <input v-model="param.name" :disabled="isViewMode || !param.enabled" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
                    </label>
                    <label class="block space-y-1.5">
                      <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Parameter type') }}</span>
                      <select v-model="param.type" :disabled="isViewMode || !param.enabled" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5">
                        <option v-for="type in PARAM_TYPES" :key="type" :value="type">{{ type }}</option>
                      </select>
                    </label>
                    <label class="flex items-end gap-2 pb-2 text-sm font-semibold">
                      <input v-model="param.required" :disabled="isViewMode || !param.enabled" type="checkbox" class="h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500 disabled:cursor-not-allowed" />
                      {{ t('MCP Editor Parameter required') }}
                    </label>
                  </div>

                  <label class="mt-3 block space-y-1.5">
                    <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Parameter description') }}</span>
                    <input v-model="param.description" :disabled="isViewMode || !param.enabled" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5" />
                  </label>

                  <label class="mt-3 block space-y-1.5">
                    <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Parameter default') }}</span>
                    <textarea
                      v-if="param.type === 'array' || param.type === 'object'"
                      v-model="param.defaultValue"
                      :disabled="isViewMode || !param.enabled"
                      class="min-h-[88px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-xs outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5"
                      :placeholder="param.type === 'array' ? '[]' : '{}'"
                    ></textarea>
                    <select v-else-if="param.type === 'boolean'" v-model="param.defaultValue" :disabled="isViewMode || !param.enabled" class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5">
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                    <input
                      v-else
                      v-model="param.defaultValue"
                      :disabled="isViewMode || !param.enabled"
                      class="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-white/5"
                      :type="param.type === 'number' || param.type === 'integer' ? 'number' : 'text'"
                    />
                  </label>
                </div>
              </div>
            </section>

            <section class="rounded-lg border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 class="text-base font-black">{{ t('MCP Editor Recorded steps') }}</h2>
                  <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Recorded steps hint') }}</p>
                </div>
                <div class="rounded-full bg-white px-4 py-1.5 text-xs font-bold text-violet-700 shadow-sm ring-1 ring-violet-100 dark:bg-white/[0.06] dark:text-violet-200 dark:ring-white/10">
                  {{ recordedSteps.length }} {{ t('MCP Editor steps') }}
                </div>
              </div>

              <div v-if="stepsLoading" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
                {{ t('MCP Editor Loading recorded steps...') }}
              </div>

              <div v-else-if="recordedSteps.length === 0" class="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
                {{ t('MCP Editor No recorded steps available for this session.') }}
              </div>

              <div v-else class="space-y-3">
                <article
                  v-for="(step, idx) in recordedSteps"
                  :key="step.id"
                  class="overflow-hidden rounded-lg border bg-white shadow-sm transition-all dark:bg-white/[0.04]"
                  :class="isRemovedStep(idx) ? 'border-rose-200 bg-rose-50/70 dark:border-rose-400/30 dark:bg-rose-500/10' : (expandedStepIndex === idx ? 'border-violet-300 shadow-lg shadow-violet-500/10 dark:border-violet-400/30' : 'border-slate-200 dark:border-white/10')"
                >
                  <div class="cursor-pointer px-4 py-4 sm:px-5" @click="toggleStep(idx)">
                    <div class="flex items-start gap-4">
                      <div
                        class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl text-xs font-extrabold"
                        :class="isRemovedStep(idx) ? 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200' : (expandedStepIndex === idx ? 'bg-violet-600 text-white' : 'bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400')"
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
                          <span
                            v-if="isRemovedStep(idx)"
                            class="rounded-full bg-rose-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-200 dark:ring-rose-400/20"
                          >
                            {{ t('MCP Editor Removed from MCP tool') }}
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
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Primary') }}</span>
                          <span class="break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatLocator(step.target) }}</span>
                        </div>
                        <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Frame') }}</span>
                          <span class="break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatFramePath(step.frame_path) }}</span>
                        </div>
                        <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                          <span class="text-xs font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor Validation') }}</span>
                          <div class="flex flex-wrap items-center gap-2">
                            <span
                              v-if="step.validation?.status"
                              class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                              :class="getValidationClass(step.validation.status)"
                            >
                              {{ getValidationLabel(step.validation.status) }}
                            </span>
                            <span class="text-xs text-slate-600 dark:text-slate-400">{{ step.validation?.details || t('MCP Editor No extra diagnostics') }}</span>
                          </div>
                        </div>
                      </div>

                      <div v-if="step.locator_candidates?.length" class="space-y-2">
                        <div class="flex items-center justify-between">
                          <p class="text-sm font-bold text-slate-900 dark:text-slate-100">{{ t('MCP Editor Locator candidates') }}</p>
                          <p class="text-xs text-slate-400 dark:text-slate-500">{{ t('MCP Editor Switching here updates the next MCP preview.') }}</p>
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
                                  {{ candidate.kind || t('MCP Editor locator') }}
                                </span>
                                <span class="text-slate-400 dark:text-slate-500">{{ t('MCP Editor Score') }} {{ candidate.score ?? '-' }}</span>
                                <span class="text-slate-400 dark:text-slate-500">{{ t('MCP Editor Strict') }} {{ candidate.strict_match_count ?? '-' }}</span>
                                <span
                                  v-if="candidate.selected"
                                  class="rounded-full bg-violet-600 px-2 py-0.5 font-semibold text-white"
                                >
                                  {{ t('MCP Editor Current') }}
                                </span>
                              </div>
                              <p class="mt-1 break-all font-mono text-xs text-slate-700 dark:text-slate-300">{{ formatLocator(candidate.locator) }}</p>
                              <p v-if="candidate.reason" class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{{ candidate.reason }}</p>
                            </div>

                            <button
                              type="button"
                              class="shrink-0 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors"
                              :class="candidate.selected || !canPromoteStepLocator(step) ? 'cursor-default border-slate-200 text-slate-400 dark:border-white/10 dark:text-slate-500' : 'border-violet-300 text-violet-700 hover:bg-violet-50 dark:border-violet-400/30 dark:text-violet-200 dark:hover:bg-violet-500/10'"
                              :disabled="candidate.selected || promotingStepIndex === idx || !canPromoteStepLocator(step)"
                              @click.stop="promoteLocator(idx, candidateIndex)"
                            >
                              {{ promotingStepIndex === idx ? t('MCP Editor Switching...') : (candidate.selected ? t('MCP Editor Current') : t('MCP Editor Use this locator')) }}
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
          <section v-else class="rounded-lg border border-dashed border-slate-300 bg-slate-50/70 p-8 dark:border-white/10 dark:bg-white/[0.03]">
            <h2 class="text-lg font-black">{{ t('MCP Editor Start from an RPA recording') }}</h2>
            <p class="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
              {{ t('MCP Editor Empty state hint') }}
            </p>
            <div class="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="button"
                class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-4 py-2 text-sm font-bold text-white"
                @click="router.push(buildRpaRecorderLocation())"
              >
                <Wand2 :size="16" />
                {{ t('MCP Editor Open RPA Recorder') }}
              </button>
              <p class="text-xs text-slate-500 dark:text-slate-400">
                {{ t('MCP Editor Tools is the management surface; recording remains the authoring flow.') }}
              </p>
            </div>
          </section>
        </section>

        <aside class="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <section ref="previewTestSection" class="rounded-lg border p-5 shadow-sm" :class="previewTestStatusClass">
            <div class="flex items-start justify-between gap-4">
              <div>
                <p class="text-sm font-black">{{ previewTestStatusLabel }}</p>
                <p class="mt-1 text-sm opacity-90">{{ previewTestStatusDescription }}</p>
              </div>
              <button
                data-preview-test-action
                class="inline-flex items-center gap-2 rounded-full border border-current/20 bg-white/85 px-4 py-2 text-sm font-semibold text-inherit dark:bg-[#17181d]"
                :disabled="testing"
                @click="runPreviewTest"
              >
                <Beaker :size="16" />
                {{ testing ? t('MCP Editor Testing...') : t('MCP Editor Run preview test') }}
              </button>
            </div>
          </section>

          <section class="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="mb-4 flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200">
                <Beaker :size="18" />
              </div>
              <div>
                <h2 class="text-base font-black">{{ t('MCP Editor Run & Test') }}</h2>
                <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Run and test hint') }}</p>
              </div>
            </div>

            <div class="mb-4 grid gap-3 sm:grid-cols-3">
              <div class="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-white/[0.03]">
                <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor steps') }}</p>
                <p class="mt-1 text-lg font-black">{{ recordedStepSummary.total }}</p>
              </div>
              <div class="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-white/[0.03]">
                <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor input fields') }}</p>
                <p class="mt-1 text-lg font-black">{{ schemaSummary.inputFields }}</p>
              </div>
              <div class="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-white/[0.03]">
                <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('MCP Editor output fields') }}</p>
                <p class="mt-1 text-lg font-black">{{ schemaSummary.outputFields }}</p>
              </div>
            </div>

            <div v-if="paramFields.length" class="grid gap-4 md:grid-cols-2">
              <label v-for="field in paramFields" :key="field.key" class="block space-y-2">
                <span class="text-sm font-semibold">{{ field.key }}<template v-if="field.required"> *</template></span>
                <select v-if="field.type === 'boolean'" v-model="argumentValues[field.key]" class="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5">
                  <option value="true">true</option>
                  <option value="false">false</option>
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

            <div v-if="showCookieSection && preview" class="mt-4 space-y-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-white/10 dark:bg-white/[0.03]">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <h3 class="text-sm font-bold">{{ t('MCP Editor Gateway test cookies') }}</h3>
                  <p class="text-xs text-slate-500 dark:text-slate-400">
                    {{ preview.requires_cookies ? t('MCP Editor This draft removed login steps, so cookies are required.') : t('MCP Editor Cookies are optional for this draft.') }}
                  </p>
                </div>
                <button v-if="!preview.requires_cookies" class="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold dark:border-white/10" @click="cookieSectionOpen = !cookieSectionOpen">
                  {{ cookieSectionOpen ? t('MCP Editor Hide cookie input') : t('MCP Editor Show cookie input') }}
                </button>
              </div>

              <div class="inline-flex rounded-full border border-slate-200 bg-slate-100 p-1 dark:border-white/10 dark:bg-white/10">
                <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'cookie_header' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'cookie_header'">{{ t('MCP Editor Cookie header') }}</button>
                <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'header_value' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'header_value'">{{ t('MCP Editor Header value') }}</button>
                <button class="rounded-full px-3 py-1.5 text-xs font-semibold" :class="cookieMode === 'playwright_json' ? 'bg-white text-slate-900 dark:bg-[#17181d] dark:text-white' : 'text-slate-600 dark:text-slate-300'" @click="cookieMode = 'playwright_json'">{{ t('MCP Editor Playwright JSON') }}</button>
              </div>

              <label v-if="cookieMode !== 'playwright_json'" class="block space-y-2">
                <span class="text-sm font-semibold">{{ t('MCP Editor Cookie domain') }}</span>
                <input v-model="cookieDomain" list="tool-editor-cookie-domain-list" class="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" placeholder="example.com" />
                <datalist id="tool-editor-cookie-domain-list">
                  <option v-for="domain in allowedCookieDomains" :key="domain" :value="domain"></option>
                </datalist>
              </label>

              <label class="block space-y-2">
                <span class="text-sm font-semibold">{{ t('MCP Editor Cookie input') }}</span>
                <textarea v-model="cookieText" class="min-h-[140px] w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 font-mono text-xs outline-none dark:border-white/10 dark:bg-white/5" :placeholder="cookieInputPlaceholder"></textarea>
                <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('MCP Editor Cookie input hint') }}</p>
              </label>
            </div>

            <div v-if="testResult" class="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-white/10 dark:bg-[#101115]">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h3 class="text-sm font-bold">{{ t('MCP Editor Latest test result') }}</h3>
                  <p class="text-xs text-slate-500 dark:text-slate-400">{{ testResult.message || '-' }}</p>
                </div>
                <span class="rounded-full px-3 py-1 text-xs font-bold" :class="testResult.success ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200' : 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'">
                  {{ testResult.success ? t('MCP Editor Success') : t('MCP Editor Failed') }}
                </span>
              </div>
              <pre class="overflow-x-auto rounded-2xl border border-slate-200 bg-white p-3 text-xs dark:border-white/10 dark:bg-[#17181d]"><code>{{ JSON.stringify(testResult, null, 2) }}</code></pre>
            </div>
          </section>

          <section class="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <h2 class="text-base font-black">{{ t('MCP Editor API & Schemas') }}</h2>
            <p class="mt-1 text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Schema hint') }}</p>
            <div v-if="preview" class="mt-4 space-y-4">
              <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-white/10 dark:bg-white/[0.03]">
                <div class="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  <span class="rounded-full bg-white px-2.5 py-1 font-bold text-slate-700 ring-1 ring-slate-200 dark:bg-white/10 dark:text-slate-200 dark:ring-white/10">
                    {{ schemaSourceLabel }}
                  </span>
                  <span v-if="preview.semantic_inference?.confidence !== null && preview.semantic_inference?.confidence !== undefined">
                    {{ t('MCP Editor Confidence') }} {{ Math.round(Number(preview.semantic_inference.confidence) * 100) }}%
                  </span>
                </div>
                <ul v-if="semanticWarnings.length" class="mt-2 list-disc pl-5 text-xs text-amber-700 dark:text-amber-200">
                  <li v-for="warning in semanticWarnings" :key="warning">{{ warning }}</li>
                </ul>
              </div>
              <div>
                <div class="mb-2 flex items-center justify-between gap-3">
                  <p class="text-sm font-semibold">{{ t('MCP Editor Input Schema') }}</p>
                  <span class="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-500 dark:bg-white/10 dark:text-slate-400">
                    {{ schemaSummary.inputFields }} {{ t('MCP Editor fields') }}
                  </span>
                </div>
                <pre class="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs dark:border-white/10 dark:bg-[#101115]"><code>{{ JSON.stringify(confirmedInputSchema, null, 2) }}</code></pre>
              </div>
              <div>
                <div class="mb-2 flex items-center justify-between gap-3">
                  <p class="text-sm font-semibold">{{ t('MCP Editor Output Schema') }}</p>
                  <span class="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-500 dark:bg-white/10 dark:text-slate-400">
                    {{ schemaSummary.outputFields }} {{ t('MCP Editor fields') }}
                  </span>
                </div>
                <textarea v-model="outputSchemaText" :disabled="isViewMode" class="min-h-[260px] w-full rounded-2xl border border-slate-200 bg-slate-50 p-3 font-mono text-xs outline-none disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/10 dark:bg-[#101115]" spellcheck="false"></textarea>
              </div>
            </div>
          </section>

          <section class="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200">
                <Shield :size="18" />
              </div>
              <div>
                <h2 class="text-base font-black">{{ t('MCP Editor Sanitize report') }}</h2>
                <p class="text-sm text-slate-500 dark:text-slate-400">{{ t('MCP Editor Sanitize report hint') }}</p>
              </div>
            </div>
            <div v-if="preview" class="mt-4 space-y-3 text-sm">
              <div>
                <p class="font-semibold">{{ t('MCP Editor Removed login steps') }}</p>
                <div v-if="removedStepDetails.length" class="mt-2 space-y-2">
                  <div
                    v-for="detail in removedStepDetails"
                    :key="detail.index"
                    class="rounded-2xl border border-rose-100 bg-rose-50 px-3 py-2 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-100"
                  >
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-bold dark:bg-white/10">
                        {{ t('MCP Editor Removed step number', { number: Number(detail.index) + 1 }) }}
                      </span>
                      <span class="text-xs font-semibold uppercase tracking-wide">{{ getActionLabel(detail.action || '') }}</span>
                    </div>
                    <p class="mt-1 text-sm font-semibold">{{ detail.description || '-' }}</p>
                    <p v-if="detail.url" class="mt-1 break-all font-mono text-[11px] opacity-75">{{ detail.url }}</p>
                  </div>
                </div>
                <p v-else class="text-slate-500 dark:text-slate-400">{{ t('MCP Editor None') }}</p>
              </div>
              <div>
                <p class="font-semibold">{{ t('MCP Editor Removed params') }}</p>
                <p class="text-slate-500 dark:text-slate-400">{{ preview.sanitize_report.removed_params.join(', ') || t('MCP Editor None') }}</p>
              </div>
              <div>
                <p class="font-semibold">{{ t('MCP Editor Warnings') }}</p>
                <ul class="list-disc pl-5 text-slate-500 dark:text-slate-400">
                  <li v-for="warning in preview.sanitize_report.warnings" :key="warning">{{ warning }}</li>
                  <li v-if="preview.sanitize_report.warnings.length === 0">{{ t('MCP Editor None') }}</li>
                </ul>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
    </div>
  </div>
</template>

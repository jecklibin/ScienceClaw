<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, Beaker, Save, Shield, Wand2 } from 'lucide-vue-next';

import {
  createRpaMcpTool,
  previewRpaMcpTool,
  testPreviewRpaMcpTool,
  type JsonSchemaObject,
  type RpaMcpExecutionResult,
  type RpaMcpPreview,
} from '@/api/rpaMcp';
import { convertCookieInputToPlaywrightCookies, type CookieInputMode } from '@/utils/rpaMcpTest';
import { showErrorToast, showSuccessToast } from '@/utils/toast';

type GatewayParamField = {
  key: string;
  type: string;
  description: string;
  required: boolean;
  defaultValue?: unknown;
};

const route = useRoute();
const router = useRouter();
const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const preview = ref<RpaMcpPreview | null>(null);
const testResult = ref<RpaMcpExecutionResult | null>(null);
const hasSuccessfulTest = ref(false);
const toolName = ref('');
const description = ref('');
const postAuthStartUrl = ref('');
const allowedDomainsText = ref('');
const outputSchemaText = ref('{}');
const cookieSectionOpen = ref(false);
const cookieMode = ref<CookieInputMode>('cookie_header');
const cookieText = ref('');
const cookieDomain = ref('');
const argumentValues = reactive<Record<string, unknown>>({});

const formatJsonBlock = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

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
const cookieInputPlaceholder = computed(() => {
  if (cookieMode.value === 'cookie_header') return 'Cookie: sid=abc; theme=dark';
  if (cookieMode.value === 'header_value') return 'sid=abc; theme=dark';
  return '[{"name":"sid","value":"abc","domain":".example.com","path":"/"}]';
});

const loadPreview = async () => {
  if (!sessionId.value) {
    showErrorToast('Missing sessionId');
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
    await loadPreview();
    showSuccessToast(testResult.value.message || 'Preview test completed');
  } catch (error: any) {
    hasSuccessfulTest.value = false;
    console.error(error);
    showErrorToast(error?.message || 'Preview test failed');
  } finally {
    testing.value = false;
  }
};

const saveTool = async () => {
  if (!sessionId.value) return;
  if (!hasSuccessfulTest.value) {
    showErrorToast('Run a successful preview test before saving this tool');
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

onMounted(loadPreview);
</script>

<template>
  <div class="min-h-screen bg-[#f5f7fb] text-slate-900 dark:bg-[#101115] dark:text-slate-100">
    <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div class="mb-6 flex items-center justify-between gap-4">
        <button class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold dark:border-white/10 dark:bg-white/5" @click="router.back()">
          <ArrowLeft :size="16" />
          Back
        </button>
        <button
          class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#8930b0] to-[#004be2] px-5 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="saving || loading || !hasSuccessfulTest"
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
              <h1 class="text-xl font-black">Convert to MCP Tool</h1>
              <p class="text-sm text-slate-500 dark:text-slate-400">This tool must pass preview test before it can be saved.</p>
            </div>
          </div>

          <div v-if="loading" class="rounded-2xl border border-dashed border-slate-300 p-8 text-sm text-slate-500 dark:border-white/10">Loading preview...</div>

          <template v-else-if="preview">
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

            <section class="rounded-3xl border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.03]">
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
                  <input v-model="cookieDomain" list="convert-cookie-domain-list" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none dark:border-white/10 dark:bg-white/5" placeholder="example.com" />
                  <datalist id="convert-cookie-domain-list">
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
          </template>
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

          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <h2 class="text-base font-black">Retained steps</h2>
            <ol v-if="preview" class="mt-4 space-y-2 text-sm text-slate-600 dark:text-slate-300">
              <li v-for="(step, index) in preview.steps" :key="`${index}-${step.description || step.action}`" class="rounded-2xl bg-slate-50 px-3 py-2 dark:bg-white/5">
                {{ index + 1 }}. {{ step.description || step.action }}
              </li>
            </ol>
          </section>
        </aside>
      </div>
    </div>
  </div>
</template>

// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';

import en from '../../locales/en';
import zh from '../../locales/zh';

const push = vi.fn();
const getRpaMcpTool = vi.fn();
const getRpaMcpExecutionPlan = vi.fn();
const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: { toolId: 'tool-1' },
    query: { mode: 'view' },
  }),
  useRouter: () => ({ push }),
}));

vi.mock('@/api/rpaMcp', () => ({
  createRpaMcpTool: vi.fn(),
  getRpaMcpTool: (...args: unknown[]) => getRpaMcpTool(...args),
  getRpaMcpExecutionPlan: (...args: unknown[]) => getRpaMcpExecutionPlan(...args),
  previewRpaMcpTool: vi.fn(),
  testPreviewRpaMcpTool: vi.fn(),
  testRpaMcpTool: vi.fn(),
  updateRpaMcpTool: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
  },
}));

vi.mock('@/utils/toast', () => ({
  showErrorToast: vi.fn(),
  showSuccessToast: vi.fn(),
}));

async function flushAsyncUpdates() {
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
}

async function mountViewPage(locale = 'en') {
  const { default: McpToolEditorPage } = await import('./McpToolEditorPage.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const app = createApp(McpToolEditorPage);
  app.use(createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'en',
    messages: { en, zh },
  }));
  app.mount(root);
  await flushAsyncUpdates();

  return { app, root };
}

describe('McpToolEditorPage view mode', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('loads the execution script only after switching to the files tab', async () => {
    getRpaMcpTool.mockResolvedValue({
      id: 'tool-1',
      enabled: true,
      name: 'Invoice Export',
      tool_name: 'invoice_export',
      description: 'Export invoices from the dashboard',
      requires_cookies: true,
      allowed_domains: ['example.com'],
      post_auth_start_url: 'https://example.com/dashboard',
      steps: [
        {
          id: 'step_1',
          action: 'click',
          description: 'Click export button',
          validation: { status: 'ok', details: '' },
          locator_candidates: [],
        },
      ],
      params: {
        query: {
          type: 'string',
          description: 'Invoice query',
          required: true,
          source_param: 'query',
        },
      },
      input_schema: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description: 'Invoice query',
          },
        },
        required: ['query'],
      },
      output_schema: { type: 'object', properties: {} },
      recommended_output_schema: { type: 'object', properties: {} },
      sanitize_report: {
        removed_steps: [],
        removed_params: [],
        warnings: [],
      },
      source: {},
    });
    getRpaMcpExecutionPlan.mockResolvedValue({
      tool_id: 'tool-1',
      generated_at: '2026-04-24T12:00:00+08:00',
      requires_cookies: true,
      compiled_steps: [],
      compiled_script: "async def run(page):\n    await page.click('text=Export invoice')\n",
      input_schema: { type: 'object', properties: {} },
      output_schema: { type: 'object', properties: {} },
      source_hash: 'hash-1',
    });

    const { app, root } = await mountViewPage('en');

    const initialText = root.textContent || '';
    expect(initialText).toContain('Overview');
    expect(initialText).toContain('Basic Info');
    const readonlyInputs = Array.from(root.querySelectorAll('input')) as HTMLInputElement[];
    expect(readonlyInputs.some((input) => input.value === 'Invoice Export')).toBe(true);
    expect(getRpaMcpExecutionPlan).not.toHaveBeenCalled();

    const filesButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('Files'));
    expect(filesButton).toBeTruthy();
    filesButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    expect(getRpaMcpExecutionPlan).toHaveBeenCalledWith('tool-1');
    expect(root.textContent || '').toContain("await page.click('text=Export invoice')");

    app.unmount();
  });
});

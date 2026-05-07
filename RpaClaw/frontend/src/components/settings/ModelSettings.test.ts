// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ModelSettings from './ModelSettings.vue';
import en from '../../locales/en';
import zh from '../../locales/zh';
import type { ModelConfig } from '../../api/models';

const listModels = vi.fn();
const createModel = vi.fn();
const updateModel = vi.fn();
const deleteModel = vi.fn();
const detectContextWindow = vi.fn();
const listCredentials = vi.fn();
const showSuccessToast = vi.fn();
const showErrorToast = vi.fn();

vi.mock('@/api/models', () => ({
  listModels: () => listModels(),
  createModel: (payload: unknown) => createModel(payload),
  updateModel: (id: string, payload: unknown) => updateModel(id, payload),
  deleteModel: (id: string) => deleteModel(id),
  detectContextWindow: (payload: unknown) => detectContextWindow(payload),
}));

vi.mock('@/api/credential', () => ({
  listCredentials: () => listCredentials(),
}));

vi.mock('@/utils/toast', () => ({
  showSuccessToast: (message: string) => showSuccessToast(message),
  showErrorToast: (message: string) => showErrorToast(message),
}));

function customModel(overrides: Partial<ModelConfig> = {}): ModelConfig {
  return {
    id: 'model-1',
    name: 'Company GPT',
    provider: 'openai',
    base_url: 'https://model.example/v1',
    api_key: '********',
    model_name: 'company-gpt',
    context_window: 131072,
    is_system: false,
    user_id: 'user-1',
    is_active: true,
    auth_config: null,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

async function flushAsyncUpdates() {
  await Promise.resolve();
  await nextTick();
  await Promise.resolve();
  await nextTick();
}

async function mountModelSettings(models: ModelConfig[] = [], locale = 'en') {
  listModels.mockResolvedValue(models);
  createModel.mockResolvedValue(customModel({ id: 'created-model' }));
  updateModel.mockResolvedValue(undefined);
  deleteModel.mockResolvedValue(undefined);
  detectContextWindow.mockResolvedValue(131072);
  listCredentials.mockResolvedValue([]);

  const root = document.createElement('div');
  document.body.appendChild(root);
  const app = createApp(ModelSettings);
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

function clickByText(root: ParentNode, text: string) {
  const button = Array.from(root.querySelectorAll('button')).find((el) => (el.textContent || '').includes(text));
  expect(button, `button containing ${text}`).toBeTruthy();
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

function clickByTitle(root: ParentNode, title: string) {
  const button = Array.from(root.querySelectorAll('button')).find((el) => el.getAttribute('title') === title);
  expect(button, `button titled ${title}`).toBeTruthy();
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

function inputs(root: ParentNode) {
  return Array.from(root.querySelectorAll('input')) as HTMLInputElement[];
}

function setInput(input: HTMLInputElement, value: string) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function inputByPlaceholder(root: ParentNode, placeholder: string) {
  const input = inputs(root).find((el) => el.placeholder === placeholder);
  expect(input, `input with placeholder ${placeholder}`).toBeTruthy();
  return input!;
}

function staticHeaderInputs() {
  const allInputs = inputs(document.body);
  return allInputs.filter((input) => input.placeholder === 'X-Gateway-Token' || input.placeholder === 'static-token');
}

describe('ModelSettings model authentication UI', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('creates a model with no extra auth by default', async () => {
    const { app, root } = await mountModelSettings();

    clickByText(root, 'Add Model');
    await nextTick();

    setInput(inputByPlaceholder(document.body, 'e.g. My GPT-4'), 'Plain GPT');
    setInput(inputByPlaceholder(document.body, 'https://api.openai.com/v1'), 'https://plain.example/v1');
    setInput(inputByPlaceholder(document.body, 'sk-...'), 'sk-test');

    clickByText(document.body, 'Save & Verify');
    await flushAsyncUpdates();

    expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Plain GPT',
      model_name: 'gpt-5.4',
      auth_config: { type: 'none' },
    }));

    app.unmount();
  });

  it('supports adding, deleting, and saving static header rows', async () => {
    const { app, root } = await mountModelSettings();

    clickByText(root, 'Add Model');
    await nextTick();
    clickByTitle(document.body, 'Authentication Config');
    await nextTick();
    clickByText(document.body, 'Static Token');
    await nextTick();
    clickByTitle(document.body, 'Add Header');
    await nextTick();
    clickByTitle(document.body, 'Add Header');
    await nextTick();

    setInput(inputByPlaceholder(document.body, 'e.g. My GPT-4'), 'Gateway GPT');
    setInput(inputByPlaceholder(document.body, 'https://api.openai.com/v1'), 'https://gateway.example/v1');
    setInput(inputByPlaceholder(document.body, 'sk-...'), 'sk-test');

    let headerInputs = staticHeaderInputs();
    setInput(headerInputs[0], 'X-Delete-Me');
    setInput(headerInputs[1], 'delete-me');
    setInput(headerInputs[2], 'X-Gateway-Token');
    setInput(headerInputs[3], 'static-token');

    const deleteButtons = Array.from(document.body.querySelectorAll('button')).filter((el) =>
      el.querySelector('svg') && (el.getAttribute('title') || '').includes('Delete'),
    );
    deleteButtons[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    clickByText(document.body, 'Save & Verify');
    await flushAsyncUpdates();

    expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
      auth_config: {
        type: 'static_headers',
        static_headers: [{ name: 'X-Gateway-Token', value: 'static-token', credential_id: undefined }],
      },
    }));

    app.unmount();
  });

  it('syncs editable header JSON with header rows and shows invalid JSON errors', async () => {
    const { app, root } = await mountModelSettings();

    clickByText(root, 'Add Model');
    await nextTick();
    clickByTitle(document.body, 'Authentication Config');
    await nextTick();
    clickByText(document.body, 'Static Token');
    await nextTick();

    const textarea = document.body.querySelector('textarea') as HTMLTextAreaElement;
    textarea.value = JSON.stringify({
      Authorization: 'Bearer imported',
      'X-Tenant': 'tenant-a',
    });
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();

    expect(inputs(document.body).some((input) => input.value === 'Authorization')).toBe(true);
    expect(inputs(document.body).some((input) => input.value === 'Bearer imported')).toBe(true);

    textarea.value = '["not-an-object"]';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();

    expect(document.body.textContent).toContain('Header JSON must be an object');

    textarea.value = '{bad json';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();

    expect(document.body.textContent).toContain('Header JSON must be an object');

    app.unmount();
  });

  it('shows configured placeholders without echoing saved static header secrets', async () => {
    const { app, root } = await mountModelSettings([
      customModel({
        auth_config: {
          version: 1,
          type: 'static_headers',
          credentials: [{ alias: 'header_authorization', credential_id: 'cred-auth' }],
          headers: { Authorization: '{{ header_authorization.password }}' },
          query: {},
        },
      }),
    ]);

    const editButton = root.querySelector<HTMLButtonElement>('button[title="Edit"]');
    expect(editButton).toBeTruthy();
    editButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    const pageText = document.body.textContent || '';
    const allInputs = inputs(document.body);
    const authValueInput = allInputs.find((input) => input.placeholder === 'Configured - leave blank to keep');

    expect(pageText).toContain('Static Token');
    expect(authValueInput).toBeTruthy();
    expect(authValueInput!.value).toBe('');
    expect(document.body.innerHTML).not.toContain('Bearer imported');
    expect(document.body.innerHTML).not.toContain('company-token');

    app.unmount();
  });

  it('supports selecting and saving dynamic token auth config', async () => {
    const { app, root } = await mountModelSettings();

    clickByText(root, 'Add Model');
    await nextTick();
    clickByTitle(document.body, 'Authentication Config');
    await nextTick();
    clickByText(document.body, 'Dynamic Token');
    await nextTick();

    setInput(inputByPlaceholder(document.body, 'e.g. My GPT-4'), 'Dynamic Gateway GPT');
    setInput(inputByPlaceholder(document.body, 'https://api.openai.com/v1'), 'https://gateway.example/v1');
    setInput(inputByPlaceholder(document.body, 'sk-...'), 'sk-test');
    clickByText(document.body, 'Save & Verify');
    await flushAsyncUpdates();

    expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
      auth_config: {
        type: 'dynamic_token',
        dynamic_token: {
          credentials: [],
          token_request: {
            method: 'POST',
            url: 'https://auth.company.com/token',
            headers: { 'Content-Type': 'application/json' },
            query: {},
            body_type: 'json',
            body: {
              client_id: 'your-client-id',
              client_secret: 'your-client-secret',
            },
          },
          inject: {
            headers: { Authorization: 'Bearer {$.data.access_token}' },
            query: {},
            body: {},
          },
        },
      },
    }));

    app.unmount();
  });
});

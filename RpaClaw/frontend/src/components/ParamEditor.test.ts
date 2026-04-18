// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('./ui/MonacoEditor.vue', () => ({
  default: {
    name: 'MonacoEditorStub',
    props: ['value', 'readOnly'],
    emits: ['change'],
    template: '<textarea data-testid="monaco-stub" :readonly="readOnly" :value="value" @input="$emit(\'change\', $event.target.value)" />',
  },
}));

vi.mock('../api/credential', () => ({
  listCredentials: vi.fn().mockResolvedValue([]),
}));

async function mountParamEditor(props: Record<string, unknown>) {
  const { default: ParamEditor } = await import('./ParamEditor.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);
  const app = createApp(ParamEditor, props);
  app.mount(root);
  await nextTick();
  return { app, root };
}

describe('ParamEditor', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.resetModules();
  });

  it('renders params.json in read-only form mode by default', async () => {
    const { app, root } = await mountParamEditor({
      content: JSON.stringify({
        query: {
          type: 'string',
          description: 'Search query',
          original_value: 'caffeine',
          required: true,
        },
      }),
      readonly: true,
    });

    expect(root.textContent).toContain('query');
    expect(root.textContent).toContain('caffeine');
    expect(root.textContent).toContain('Text Mode');
    expect(root.textContent).not.toContain('Add Parameter');
    expect(root.querySelector('input')).toBeNull();
    expect(root.querySelector('select')).toBeNull();

    app.unmount();
  });

  it('allows read-only params to switch to text mode without enabling editing', async () => {
    const { app, root } = await mountParamEditor({
      content: '{"query":{"type":"string","original_value":"caffeine"}}',
      readonly: true,
    });

    const toggle = Array.from(root.querySelectorAll('button')).find((button) => (
      button.textContent?.includes('Text Mode')
    ));
    toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    const editor = root.querySelector<HTMLTextAreaElement>('[data-testid="monaco-stub"]');
    expect(editor).not.toBeNull();
    expect(editor?.readOnly).toBe(true);

    app.unmount();
  });
});

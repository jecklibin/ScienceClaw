// @vitest-environment jsdom

import { createApp, defineComponent, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CustomDialog from './CustomDialog.vue';
import { useDialog } from '@/composables/useDialog';

const Harness = defineComponent({
  name: 'CustomDialogHarness',
  components: { CustomDialog },
  props: {
    onConfirm: {
      type: Function,
      required: true,
    },
  },
  setup(props) {
    const { showConfirmDialog } = useDialog();

    const open = () => {
      showConfirmDialog({
        title: 'Delete MCP tool',
        content: 'This MCP tool will no longer be available in chat sessions.',
        confirmText: 'Delete',
        cancelText: 'Cancel',
        confirmType: 'danger',
        onConfirm: props.onConfirm as () => void,
      });
    };

    return { open };
  },
  template: `
    <button id="open-dialog" @click="open">Open</button>
    <CustomDialog />
  `,
});

async function mountHarness(onConfirm = vi.fn()) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const app = createApp(Harness, { onConfirm });
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: {} } }));
  app.mount(root);
  await nextTick();

  return { app, root, onConfirm };
}

describe('CustomDialog', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('renders destructive confirmations as an accessible custom alert dialog', async () => {
    const { app } = await mountHarness();

    document.querySelector<HTMLButtonElement>('#open-dialog')?.click();
    await nextTick();

    const dialog = document.querySelector('[role="alertdialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain('Delete MCP tool');
    expect(dialog?.textContent).toContain('This MCP tool will no longer be available');
    expect(dialog?.querySelector('[data-testid="dialog-tone-icon"]')).not.toBeNull();

    app.unmount();
  });

  it('runs the confirm callback from the custom dialog button', async () => {
    const onConfirm = vi.fn();
    const { app } = await mountHarness(onConfirm);

    document.querySelector<HTMLButtonElement>('#open-dialog')?.click();
    await nextTick();
    Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.trim() === 'Delete')
      ?.click();
    await nextTick();

    expect(onConfirm).toHaveBeenCalledTimes(1);

    app.unmount();
  });
});

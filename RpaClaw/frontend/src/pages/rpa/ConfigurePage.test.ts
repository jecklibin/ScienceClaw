// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

const push = vi.fn();
const get = vi.fn();
const post = vi.fn();
const deleteRequest = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { sessionId: 'session-1' } }),
  useRouter: () => ({ push }),
}));

vi.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    delete: (...args: unknown[]) => deleteRequest(...args),
  },
}));

vi.mock('@/components/rpa/RpaFlowGuide.vue', () => ({
  default: {
    name: 'RpaFlowGuideStub',
    props: ['secondaryActions'],
    emits: ['secondary-action'],
    template: `
      <div data-testid="flow-guide">
        <button
          v-for="action in secondaryActions"
          :key="action.id"
          type="button"
          :disabled="action.disabled"
          @click="$emit('secondary-action', action.id)"
        >
          {{ action.label }}
        </button>
      </div>
    `,
  },
}));

vi.mock('@/components/rpa/RpaDiscardRecordingDialog.vue', () => ({
  default: {
    name: 'RpaDiscardRecordingDialogStub',
    template: '<div />',
  },
}));

vi.mock('@/components/rpa/RpaStepTimeline.vue', () => ({
  default: {
    name: 'RpaStepTimelineStub',
    props: ['steps'],
    template: '<div data-testid="step-timeline">{{ steps.length }} steps</div>',
  },
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: {
    name: 'DialogStub',
    props: ['open'],
    template: '<div v-if="open" data-testid="script-dialog"><slot /></div>',
  },
  DialogContent: {
    name: 'DialogContentStub',
    template: '<div><slot /></div>',
  },
  DialogHeader: {
    name: 'DialogHeaderStub',
    template: '<div><slot /></div>',
  },
  DialogTitle: {
    name: 'DialogTitleStub',
    template: '<div><slot /></div>',
  },
}));

const flushAsyncUpdates = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
};

const mountConfigurePage = async () => {
  const { default: ConfigurePage } = await import('./ConfigurePage.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const app = createApp(ConfigurePage);
  app.mount(root);
  await flushAsyncUpdates();

  return { app, root };
};

describe('ConfigurePage script preview entry', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('keeps the top preview action without rendering an inline script preview panel', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/credentials') return Promise.resolve({ data: { credentials: [] } });
      return Promise.resolve({
        data: {
          session: {
            url: 'https://github.com/trending',
            steps: [
              { id: 'step-1', action: 'goto', url: 'https://github.com/trending' },
            ],
          },
        },
      });
    });
    post.mockResolvedValue({ data: { script: 'print("generated script")' } });

    const { app, root } = await mountConfigurePage();

    expect(root.textContent).toContain('预览脚本');
    expect(root.textContent).not.toContain('脚本预览');
    expect(root.textContent).not.toContain('generated script');

    app.unmount();
  });
});

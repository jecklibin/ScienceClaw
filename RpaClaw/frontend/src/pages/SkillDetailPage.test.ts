// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';

import en from '../locales/en';
import zh from '../locales/zh';

const back = vi.fn();
const getSkillFiles = vi.fn();
const getSkillDetail = vi.fn();
const getSkills = vi.fn();
const readSkillFile = vi.fn();
const writeSkillFile = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { skillName: 'recorded_skill' } }),
  useRouter: () => ({ back }),
}));

vi.mock('../api/agent', () => ({
  getSkillFiles: (...args: unknown[]) => getSkillFiles(...args),
  getSkillDetail: (...args: unknown[]) => getSkillDetail(...args),
  getSkills: (...args: unknown[]) => getSkills(...args),
  readSkillFile: (...args: unknown[]) => readSkillFile(...args),
  writeSkillFile: (...args: unknown[]) => writeSkillFile(...args),
}));

vi.mock('../components/FileViewer.vue', () => ({
  default: {
    name: 'FileViewerStub',
    template: '<div data-testid="file-viewer-stub">File viewer</div>',
  },
}));

vi.mock('../components/ParamEditor.vue', () => ({
  default: {
    name: 'ParamEditorStub',
    template: '<div data-testid="param-editor-stub">Param editor</div>',
  },
}));

async function flushAsyncUpdates() {
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
}

async function mountSkillDetailPage(locale = 'en') {
  const { default: SkillDetailPage } = await import('./SkillDetailPage.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const app = createApp(SkillDetailPage);
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

describe('SkillDetailPage recorded overview mode', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('shows overview first for recorded skills and defers file reads', async () => {
    getSkillFiles.mockResolvedValue([
      { name: 'skill.meta.json', path: 'skill.meta.json', type: 'file' },
      { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
      { name: 'skill.py', path: 'skill.py', type: 'file' },
    ]);
    getSkillDetail.mockResolvedValue({
      kind: 'skill',
      mode: 'recorded-overview',
      can_use_overview: true,
      name: 'recorded_skill',
      description: 'Recorded flow',
      entry_script: 'skill.py',
      generated_at: '2026-04-24T12:00:00+08:00',
      params: {
        query: {
          type: 'string',
          description: 'Search query',
          required: true,
        },
      },
      steps: [
        {
          id: 'step_1',
          action: 'goto',
          description: 'Open dashboard',
        },
      ],
      artifacts: ['SKILL.md', 'skill.py', 'params.json'],
      files: [
        { name: 'skill.meta.json', path: 'skill.meta.json', type: 'file' },
        { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
        { name: 'skill.py', path: 'skill.py', type: 'file' },
      ],
    });
    getSkills.mockResolvedValue([
      { name: 'recorded_skill', builtin: false },
    ]);

    const { app, root } = await mountSkillDetailPage('en');

    const text = root.textContent || '';
    expect(text).toContain('Overview');
    expect(text).toContain('Files');
    expect(text).toContain('recorded_skill');
    expect(readSkillFile).not.toHaveBeenCalled();

    app.unmount();
  });
});

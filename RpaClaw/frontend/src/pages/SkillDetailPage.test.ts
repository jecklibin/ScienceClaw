// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';

import en from '../locales/en';
import zh from '../locales/zh';

const back = vi.fn();
const replace = vi.fn();
const getSkillFiles = vi.fn();
const getSkillDetail = vi.fn();
const getSkills = vi.fn();
const readSkillFile = vi.fn();
const writeSkillFile = vi.fn();
const updateSkillOverview = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { skillName: 'recorded_skill' } }),
  useRouter: () => ({ back, replace }),
}));

vi.mock('../api/agent', () => ({
  getSkillFiles: (...args: unknown[]) => getSkillFiles(...args),
  getSkillDetail: (...args: unknown[]) => getSkillDetail(...args),
  getSkills: (...args: unknown[]) => getSkills(...args),
  readSkillFile: (...args: unknown[]) => readSkillFile(...args),
  writeSkillFile: (...args: unknown[]) => writeSkillFile(...args),
  updateSkillOverview: (...args: unknown[]) => updateSkillOverview(...args),
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
    props: ['content', 'readonly'],
    emits: ['change'],
    template: `
      <div data-testid="param-editor-stub">
        <span>{{ readonly ? 'readonly' : 'editable' }}</span>
        <button
          data-testid="param-change"
          @click="$emit('change', '{&quot;keyword&quot;:{&quot;type&quot;:&quot;string&quot;,&quot;description&quot;:&quot;Search keyword&quot;,&quot;required&quot;:true,&quot;original_value&quot;:&quot;cancer&quot;,&quot;sensitive&quot;:false}}')"
        >
          Change params
        </button>
      </div>
    `,
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

  it('updates overview metadata and navigates to the renamed skill identifier', async () => {
    getSkillFiles.mockResolvedValue([
      { name: 'skill.meta.json', path: 'skill.meta.json', type: 'file' },
      { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
      { name: 'params.json', path: 'params.json', type: 'file' },
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
      steps: [],
      artifacts: ['SKILL.md', 'skill.py', 'params.json'],
      files: [
        { name: 'skill.meta.json', path: 'skill.meta.json', type: 'file' },
        { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
        { name: 'params.json', path: 'params.json', type: 'file' },
        { name: 'skill.py', path: 'skill.py', type: 'file' },
      ],
    });
    getSkills.mockResolvedValue([{ name: 'recorded_skill', builtin: false }]);
    updateSkillOverview.mockResolvedValue({
      skill_name: 'renamed_skill',
      renamed: true,
    });

    const { app, root } = await mountSkillDetailPage('en');

    const editButton = Array.from(root.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Edit')) as HTMLButtonElement;
    editButton.click();
    await nextTick();
    expect(root.textContent).toContain('Editing');
    expect(root.textContent).toContain('Skill metadata');
    expect(root.textContent).toContain('Synced files');
    expect(root.textContent).not.toContain('SYNC TARGETS');

    const nameInput = root.querySelector('[data-testid="skill-overview-name"]') as HTMLInputElement;
    nameInput.value = 'renamed_skill';
    nameInput.dispatchEvent(new Event('input'));
    await nextTick();
    expect(root.textContent).toContain('Edited');

    const descriptionInput = root.querySelector('[data-testid="skill-overview-description"]') as HTMLTextAreaElement;
    descriptionInput.value = 'Updated flow';
    descriptionInput.dispatchEvent(new Event('input'));

    (root.querySelector('[data-testid="param-change"]') as HTMLButtonElement).click();
    await nextTick();

    const saveButton = Array.from(root.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Save')) as HTMLButtonElement;
    saveButton.click();
    await flushAsyncUpdates();

    expect(updateSkillOverview).toHaveBeenCalledWith('recorded_skill', {
      name: 'renamed_skill',
      description: 'Updated flow',
      params: {
        keyword: {
          type: 'string',
          description: 'Search keyword',
          required: true,
          original_value: 'cancer',
          sensitive: false,
        },
      },
    });
    expect(replace).toHaveBeenCalledWith('/chat/skills/renamed_skill');
    expect(getSkillFiles).toHaveBeenCalledTimes(1);

    app.unmount();
  });
});

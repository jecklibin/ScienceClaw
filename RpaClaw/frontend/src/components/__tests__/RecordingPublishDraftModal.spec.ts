// @vitest-environment jsdom

import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/composables/useI18n'
import type { SkillPublishDraft } from '@/types/recording'

const draft: SkillPublishDraft = {
  id: 'draft_run_1',
  run_id: 'run_1',
  publish_target: 'skill',
  skill_name: 'download_and_convert_report',
  display_title: 'Download and convert business report',
  description: 'Download a business report and convert it to CSV.',
  trigger_examples: ['Download and convert the business report'],
  inputs: [{ name: 'search', type: 'string', required: true, description: 'Search keyword' }],
  outputs: [{ name: 'detail_table', type: 'json', description: 'Normalized detail rows' }],
  credentials: [{ name: 'github_cookie', type: 'browser_session', description: 'GitHub browser session' }],
  segments: [
    {
      id: 'segment_1',
      kind: 'rpa',
      title: 'Download report',
      purpose: 'Download a report from the web page',
      status: 'tested',
      input_count: 0,
      output_count: 1,
    },
    {
      id: 'segment_2',
      kind: 'script',
      title: 'Convert report',
      purpose: 'Convert the downloaded file to CSV',
      status: 'tested',
      input_count: 1,
      output_count: 1,
    },
  ],
  warnings: [],
}

function mountModal(props: Record<string, unknown>) {
  const root = document.createElement('div')
  document.body.appendChild(root)
  return import('../RecordingPublishDraftModal.vue').then(({ default: RecordingPublishDraftModal }) => {
    const app = createApp(RecordingPublishDraftModal, props)
    i18n.global.locale.value = 'en'
    app.use(i18n)
    app.mount(root)
    return { app, root }
  })
}

describe('RecordingPublishDraftModal', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders editable final skill metadata and segment list', async () => {
    const { app, root } = await mountModal({
      visible: true,
      draft,
      saving: false,
    })
    await nextTick()

    expect(root.textContent).toContain('Publish as Skill')
    expect(root.textContent).toContain('Download report')
    expect(root.textContent).toContain('Convert report')
    expect(root.querySelector<HTMLInputElement>('[data-testid="publish-skill-name"]')?.value).toBe(
      'download_and_convert_report',
    )

    app.unmount()
  })

  it('renders MCP tool wording for tool publish drafts', async () => {
    const { app, root } = await mountModal({
      visible: true,
      draft: { ...draft, publish_target: 'tool' },
      saving: false,
    })
    await nextTick()

    expect(root.textContent).toContain('Publish as MCP Tool')
    expect(root.textContent).toContain('Tool name')
    expect(root.textContent).toContain('Workflow inputs')
    expect(root.textContent).toContain('search')
    expect(root.textContent).toContain('Workflow outputs')
    expect(root.textContent).toContain('detail_table')
    expect(root.textContent).toContain('Credential requirements')
    expect(root.textContent).toContain('github_cookie')
    expect(root.querySelector<HTMLButtonElement>('[data-testid="publish-save"]')?.textContent).toContain('Save MCP Tool')

    app.unmount()
  })

  it('emits save with edited draft', async () => {
    const onSave = vi.fn()
    const { app, root } = await mountModal({
      visible: true,
      draft,
      saving: false,
      onSave,
    })
    await nextTick()

    const input = root.querySelector<HTMLInputElement>('[data-testid="publish-skill-name"]')
    expect(input).toBeTruthy()
    input!.value = 'business_report_flow'
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    root.querySelector<HTMLButtonElement>('[data-testid="publish-save"]')?.click()
    await nextTick()

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      skill_name: 'business_report_flow',
    }))

    app.unmount()
  })
})

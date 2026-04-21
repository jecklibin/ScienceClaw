// @vitest-environment jsdom

import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SkillPublishDraft } from '@/types/recording'

const draft: SkillPublishDraft = {
  id: 'draft_run_1',
  run_id: 'run_1',
  publish_target: 'skill',
  skill_name: 'download_and_convert_report',
  display_title: '下载并转换业务报表',
  description: '自动下载业务报表并转换为 CSV。',
  trigger_examples: ['帮我下载并转换业务报表'],
  inputs: [],
  outputs: [],
  credentials: [],
  segments: [
    {
      id: 'segment_1',
      kind: 'rpa',
      title: '下载报表',
      purpose: '从网页下载报表',
      status: 'tested',
      input_count: 0,
      output_count: 1,
    },
    {
      id: 'segment_2',
      kind: 'script',
      title: '转换报表',
      purpose: '将下载文件转换为 CSV',
      status: 'tested',
      input_count: 1,
      output_count: 1,
    },
  ],
  warnings: [],
}

describe('RecordingPublishDraftModal', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders editable final skill metadata and segment list', async () => {
    const { default: RecordingPublishDraftModal } = await import('../RecordingPublishDraftModal.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingPublishDraftModal, {
      visible: true,
      draft,
      saving: false,
    })
    app.mount(root)
    await nextTick()

    expect(root.textContent).toContain('发布为技能')
    expect(root.textContent).toContain('下载报表')
    expect(root.textContent).toContain('转换报表')
    expect(root.querySelector<HTMLInputElement>('[data-testid="publish-skill-name"]')?.value).toBe(
      'download_and_convert_report',
    )

    app.unmount()
  })

  it('emits save with edited draft', async () => {
    const { default: RecordingPublishDraftModal } = await import('../RecordingPublishDraftModal.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)
    const onSave = vi.fn()

    const app = createApp(RecordingPublishDraftModal, {
      visible: true,
      draft,
      saving: false,
      onSave,
    })
    app.mount(root)
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

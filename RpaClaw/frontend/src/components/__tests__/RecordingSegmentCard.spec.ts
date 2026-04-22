// @vitest-environment jsdom

import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const messages: Record<string, string> = {
        'Workflow Segment': 'Workflow Segment',
        'Script Segment': 'Script Segment',
        'Tested': 'tested',
        'segment steps count': `${params?.count} 步`,
        'segment params count': `${params?.count} 参数`,
        'segment inputs count': `输入 ${params?.count}`,
        'segment outputs count': `输出 ${params?.count}`,
        'Collapse': '收起',
        'Expand': '展开',
        'Recording Input': '输入',
        'Recording Output': '输出',
        'Recording Parameter': '参数',
        'I/O Mapping': '输入输出映射',
      }
      return messages[key] || key
    },
  }),
}))

vi.mock('@/api/recording', () => ({
  promoteRecordingStepLocator: vi.fn(),
  promoteRecordingSegmentStepLocator: vi.fn(),
  getRecordingSegmentMappingSources: vi.fn(),
  updateRecordingSegmentBindings: vi.fn(),
}))

describe('RecordingSegmentCard', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('renders a collapsed recording summary by default and expands steps on demand', async () => {
    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      summary: {
        segment_id: 'seg-1',
        intent: 'Download GitHub trend',
        kind: 'rpa',
        status: 'completed',
        params: {
          keyword: { original_value: 'paper', sensitive: false },
          password: { original_value: 'secret', sensitive: true, credential_id: 'cred-1' },
        },
        auth_config: { credential_ids: ['cred-1'] },
        testing_status: 'passed',
        artifacts: [],
        steps: [
          {
            id: 'step-1',
            step_index: 0,
            action: 'navigate',
            description: 'Open GitHub Trending',
            target: 'https://github.com/trending',
          },
        ],
      },
    })
    app.mount(root)
    await nextTick()

    expect(root.textContent).toContain('Download GitHub trend')
    expect(root.textContent).toContain('1 步')
    expect(root.textContent).toContain('2 参数')
    expect(root.textContent).toContain('tested')
    expect(root.textContent).not.toContain('Open GitHub Trending')

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()

    expect(root.textContent).toContain('Open GitHub Trending')
    app.unmount()
  })

  it('renders long step lists inside a compact scroll container', async () => {
    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      summary: {
        segment_id: 'seg-scroll',
        intent: 'Long workflow',
        kind: 'rpa',
        status: 'completed',
        artifacts: [],
        steps: Array.from({ length: 12 }, (_, index) => ({
          id: `step-${index + 1}`,
          step_index: index,
          action: 'click',
          description: `Step ${index + 1}`,
          target: `target-${index + 1}`,
        })),
      },
    })
    app.mount(root)
    await nextTick()

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()

    const stepsList = root.querySelector<HTMLElement>('[data-testid="recording-segment-steps"]')
    expect(stepsList).toBeTruthy()
    expect(stepsList?.className).toContain('max-h-48')
    expect(stepsList?.className).toContain('overflow-y-auto')

    app.unmount()
  })

  it('renders a script segment as a workflow segment card', async () => {
    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      summary: {
        segment_id: 'segment_2',
        kind: 'script',
        title: '转换报表',
        description: '将下载文件转换为 CSV',
        artifacts: [],
        steps: [],
        params: {},
        testing_status: 'passed',
        inputs: [{ name: 'source_file', type: 'file' }],
        outputs: [{ name: 'converted_csv', type: 'file' }],
      },
    })
    app.mount(root)
    await nextTick()

    expect(root.textContent).toContain('Script Segment')
    expect(root.textContent).toContain('转换报表')
    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()
    expect(root.textContent).toContain('source_file')
    expect(root.textContent).toContain('converted_csv')

    app.unmount()
  })

  it('loads mapping sources and submits quick-bind updates for inputs', async () => {
    const recordingApi = await import('@/api/recording')
    vi.mocked(recordingApi.getRecordingSegmentMappingSources).mockResolvedValue({
      sourcePool: {
        recommended: [],
        segmentOutputs: [
          {
            id: 'seg-1:project_name',
            sourceType: 'segment_output',
            sourceRef: 'seg-1.outputs.project_name',
            segmentId: 'seg-1',
            segmentTitle: '获取项目名称',
            name: 'project_name',
            valueType: 'string',
            preview: '提取项目名称',
          },
        ],
        artifacts: [],
        workflowParams: [],
      },
    } as any)
    vi.mocked(recordingApi.updateRecordingSegmentBindings).mockResolvedValue({
      run: { id: 'run-1', status: 'ready_for_next_segment', type: 'mixed' },
      summary: {
        segment_id: 'seg-2',
        kind: 'script',
        title: '搜索项目',
        description: '搜索项目',
        artifacts: [],
        steps: [],
        params: {},
        inputs: [
          {
            name: 'search',
            type: 'string',
            source: 'segment_output',
            source_ref: 'seg-1.outputs.project_name',
          },
        ],
        outputs: [],
      },
    } as any)

    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)
    const onSegmentUpdated = vi.fn()

    const app = createApp(RecordingSegmentCard, {
      sessionId: 'session-1',
      runId: 'run-1',
      summaries: [
        {
          segment_id: 'seg-1',
          title: '获取项目名称',
          artifacts: [],
          steps: [{ id: 'step-1', action: 'extract_text', result_key: 'project_name' }],
          outputs: [{ name: 'project_name', type: 'string' }],
        },
      ],
      summary: {
        segment_id: 'seg-2',
        kind: 'script',
        title: '搜索项目',
        description: '搜索项目',
        artifacts: [],
        steps: [],
        params: {},
        inputs: [{ name: 'search', type: 'string' }],
        outputs: [],
      },
      onSegmentUpdated,
    })
    app.mount(root)
    await nextTick()

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()
    await Promise.resolve()
    await nextTick()

    const select = root.querySelector<HTMLSelectElement>('[data-testid="binding-select-search"]')
    expect(select).toBeTruthy()
    expect(select?.textContent).toContain('获取项目名称')

    select!.value = 'seg-1.outputs.project_name'
    select!.dispatchEvent(new Event('change'))
    await Promise.resolve()
    await nextTick()

    expect(recordingApi.getRecordingSegmentMappingSources).toHaveBeenCalledWith('session-1', 'run-1', 'seg-2')
    expect(recordingApi.updateRecordingSegmentBindings).toHaveBeenCalledWith('session-1', 'run-1', 'seg-2', [
      expect.objectContaining({
        name: 'search',
        source: 'segment_output',
        source_ref: 'seg-1.outputs.project_name',
      }),
    ])
    expect(onSegmentUpdated).toHaveBeenCalledWith(
      expect.objectContaining({
        run: expect.objectContaining({ id: 'run-1' }),
        summaries: [
          expect.objectContaining({
            segment_id: 'seg-2',
            inputs: [expect.objectContaining({ source_ref: 'seg-1.outputs.project_name' })],
          }),
        ],
      }),
    )

    app.unmount()
  })

  it('opens a mapping drawer for deeper io editing', async () => {
    const recordingApi = await import('@/api/recording')
    vi.mocked(recordingApi.getRecordingSegmentMappingSources).mockResolvedValue({
      sourcePool: {
        recommended: [],
        segmentOutputs: [
          {
            id: 'seg-1:project_name',
            sourceType: 'segment_output',
            sourceRef: 'seg-1.outputs.project_name',
            segmentId: 'seg-1',
            segmentTitle: '获取项目名称',
            name: 'project_name',
            valueType: 'string',
            preview: '提取项目名称',
          },
        ],
        artifacts: [],
        workflowParams: [],
      },
    } as any)

    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)

    const app = createApp(RecordingSegmentCard, {
      sessionId: 'session-1',
      runId: 'run-1',
      summaries: [],
      summary: {
        segment_id: 'seg-2',
        kind: 'script',
        title: '搜索项目',
        description: '搜索项目',
        artifacts: [],
        steps: [],
        params: {},
        inputs: [{ name: 'search', type: 'string' }],
        outputs: [],
      },
    })
    app.mount(root)
    await nextTick()

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await Promise.resolve()
    await nextTick()

    root.querySelector<HTMLButtonElement>('[data-testid="open-mapping-drawer"]')?.click()
    await nextTick()

    expect(root.textContent).toContain('输入输出映射')
    expect(root.textContent).toContain('获取项目名称')

    app.unmount()
  })

  it('switches locator candidates by falling back to the rendered step index', async () => {
    const recordingApi = await import('@/api/recording')
    vi.mocked(recordingApi.promoteRecordingSegmentStepLocator).mockResolvedValue({
      run: { id: 'run-1', status: 'ready_for_next_segment', type: 'rpa' },
      summary: {
        segment_id: 'seg-1',
        rpa_session_id: 'rpa-1',
        kind: 'rpa',
        title: '修复定位器',
        artifacts: [],
        steps: [
          {
            id: 'step-1',
            step_index: 0,
            action: 'click',
            description: '点击保存',
            target: '{"method":"css","value":"button.save"}',
            locator_candidates: [
              { kind: 'role', selected: false, locator: { method: 'role', role: 'button', name: 'Save' } },
              { kind: 'css', selected: true, locator: { method: 'css', value: 'button.save' } },
            ],
            validation: { status: 'ok', selected_candidate_index: 1 },
          },
        ],
      },
      step: {
        id: 'step-1',
        step_index: 0,
        action: 'click',
        description: '点击保存',
        target: '{"method":"css","value":"button.save"}',
        locator_candidates: [
          { kind: 'role', selected: false, locator: { method: 'role', role: 'button', name: 'Save' } },
          { kind: 'css', selected: true, locator: { method: 'css', value: 'button.save' } },
        ],
        validation: { status: 'ok', selected_candidate_index: 1 },
      },
    } as any)

    const { default: RecordingSegmentCard } = await import('@/components/RecordingSegmentCard.vue')
    const root = document.createElement('div')
    document.body.appendChild(root)
    const onSegmentUpdated = vi.fn()

    const app = createApp(RecordingSegmentCard, {
      sessionId: 'chat-session-1',
      runId: 'run-1',
      summary: {
        segment_id: 'seg-1',
        rpa_session_id: 'rpa-1',
        kind: 'rpa',
        title: '修复定位器',
        artifacts: [],
        steps: [
          {
            id: 'step-1',
            action: 'click',
            description: '点击保存',
            target: '{"method":"role","role":"button","name":"Save"}',
            locator_candidates: [
              { kind: 'role', selected: true, locator: { method: 'role', role: 'button', name: 'Save' } },
              { kind: 'css', selected: false, locator: { method: 'css', value: 'button.save' } },
            ],
            validation: { status: 'fallback', selected_candidate_index: 0 },
          },
        ],
      },
      onSegmentUpdated,
    })
    app.mount(root)
    await nextTick()

    root.querySelector<HTMLButtonElement>('[data-testid="recording-segment-toggle"]')?.click()
    await nextTick()
    const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
    buttons.find((button) => button.textContent?.includes('css'))?.click()
    await Promise.resolve()
    await nextTick()

    expect(recordingApi.promoteRecordingSegmentStepLocator).toHaveBeenCalledWith(
      'chat-session-1',
      'run-1',
      'seg-1',
      0,
      1,
      'rpa-1',
    )
    expect(root.textContent).toContain('button.save')
    expect(onSegmentUpdated).toHaveBeenCalledWith(
      expect.objectContaining({
        run: expect.objectContaining({ id: 'run-1' }),
        summaries: [expect.objectContaining({ segment_id: 'seg-1' })],
      }),
    )

    app.unmount()
  })
})

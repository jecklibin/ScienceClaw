import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'

import RecorderWorkbenchShell from '@/components/recorder/RecorderWorkbenchShell.vue'

describe('RecorderWorkbenchShell', () => {
  it('renders steps, assistant panel, canvas stage, and testing state from shared shell props', async () => {
    const app = createSSRApp(RecorderWorkbenchShell, {
      title: 'Recording Workbench',
      subtitle: '录制业务流程技能',
      steps: [{ id: '1', title: '下载按钮', description: '点击下载按钮', status: 'completed' }],
      messages: [{ role: 'assistant', text: '已准备开始录制', time: '10:00', status: 'done' }],
      testingState: { status: 'idle' },
      address: 'https://example.com',
      tabs: [{ tab_id: 'tab-1', title: 'Example', url: 'https://example.com', active: true }],
      showCanvas: false,
    })

    const html = await renderToString(app)

    expect(html).toContain('Recording Workbench')
    expect(html).toContain('下载按钮')
    expect(html).toContain('已准备开始录制')
    expect(html).toContain('测试验证')
    expect(html).toContain('Example')
  })
})

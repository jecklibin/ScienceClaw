export type InlineSandboxPreviewMode = 'browser' | 'none';

const BROWSER_TOOLS = new Set([
  'sandbox_get_browser_info',
  'sandbox_browser_screenshot',
  'sandbox_browser_execute_action',
]);

const SANDBOX_TOOLS = new Set([
  'execute',
  'sandbox_execute_bash',
  'sandbox_execute_code',
  'sandbox_file_operations',
  'sandbox_str_replace_editor',
  'sandbox_get_context',
  'sandbox_get_packages',
  'sandbox_convert_to_markdown',
  'sandbox_exec',
]);

export interface InlineSandboxToolItem {
  type: string;
  tool?: {
    function?: string;
    name?: string;
    status?: string;
    tool_meta?: {
      sandbox?: boolean;
    };
  };
}

const canInspectSandboxBrowser = (toolFunction: string, isSandboxProxy = false) => {
  if (!toolFunction) return false;
  if (isSandboxProxy) return true;
  if (BROWSER_TOOLS.has(toolFunction) || SANDBOX_TOOLS.has(toolFunction)) return true;
  return toolFunction.startsWith('browser_') || toolFunction.startsWith('terminal_') || toolFunction.startsWith('sandbox_');
};

export const getInlineSandboxPreviewMode = (items: InlineSandboxToolItem[] = []): InlineSandboxPreviewMode => {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== 'tool' || !item.tool) continue;

    const fn = item.tool.function || item.tool.name || '';
    if (canInspectSandboxBrowser(fn, !!item.tool.tool_meta?.sandbox)) return 'browser';
  }

  return 'none';
};

export const hasActiveInlineSandboxPreviewTool = (items: InlineSandboxToolItem[] = []): boolean => {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== 'tool' || !item.tool) continue;

    const fn = item.tool.function || item.tool.name || '';
    if (!canInspectSandboxBrowser(fn, !!item.tool.tool_meta?.sandbox)) continue;

    if (item.tool.status === 'calling') {
      return true;
    }
  }

  return false;
};

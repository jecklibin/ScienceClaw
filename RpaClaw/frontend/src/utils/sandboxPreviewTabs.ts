import type { SandboxPreviewMode } from './sandbox';

export type SandboxPreviewVariant = 'panel' | 'inline';
export type SandboxPreviewTabId = 'terminal' | 'browser';

export interface SandboxPreviewTab {
  id: SandboxPreviewTabId;
  label: string;
}

export const getSandboxPreviewTabs = (
  variant: SandboxPreviewVariant,
  mode: SandboxPreviewMode,
): SandboxPreviewTab[] => {
  if (variant === 'inline') {
    return [{ id: 'browser', label: 'Browser' }];
  }

  if (mode === 'browser') {
    return [{ id: 'browser', label: 'Browser' }];
  }

  return [{ id: 'terminal', label: 'Terminal' }];
};

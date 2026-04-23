import { describe, expect, it } from 'vitest';
import {
  getInlineSandboxPreviewMode,
  hasActiveInlineSandboxPreviewTool,
} from './inlineSandboxPreview';

describe('getInlineSandboxPreviewMode', () => {
  it('returns browser when a browser tool is active in the current turn', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'calling',
        },
      },
    ])).toBe('browser');
  });

  it('returns browser for sandbox terminal tools because the sandbox browser can be inspected live', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'execute',
          name: 'execute',
          status: 'calling',
        },
      },
    ])).toBe('browser');
  });

  it('keeps browser mode while the browser tool result is still the latest sandbox preview source', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'called',
        },
      },
    ])).toBe('browser');
  });

  it('treats a calling browser tool as still actively running', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'calling',
        },
      },
    ])).toBe(true);
  });

  it('stops treating the preview as active once relevant tools are all called', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'called',
        },
      },
      {
        type: 'thinking',
      },
    ])).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';

import { resolveInitialToolsTab } from './toolsPage';

describe('resolveInitialToolsTab', () => {
  it('defaults to the MCP tab when no explicit tab is provided', () => {
    expect(resolveInitialToolsTab(undefined)).toBe('mcp');
    expect(resolveInitialToolsTab(null)).toBe('mcp');
    expect(resolveInitialToolsTab('')).toBe('mcp');
  });

  it('preserves supported explicit tabs', () => {
    expect(resolveInitialToolsTab('mcp')).toBe('mcp');
    expect(resolveInitialToolsTab('external')).toBe('external');
  });

  it('falls back to MCP for unsupported values', () => {
    expect(resolveInitialToolsTab('custom')).toBe('mcp');
    expect(resolveInitialToolsTab(['external'])).toBe('mcp');
  });
});

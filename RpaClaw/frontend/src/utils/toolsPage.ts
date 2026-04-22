export type ToolsTab = 'external' | 'mcp';

export const resolveInitialToolsTab = (value: unknown): ToolsTab => {
  if (value === 'external' || value === 'mcp') {
    return value;
  }
  return 'mcp';
};

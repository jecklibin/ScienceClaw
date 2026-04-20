type McpServerLike = {
  id?: string;
  scope?: string;
  server_key?: string;
  description?: string;
  enabled?: boolean;
  default_enabled?: boolean;
  session_mode?: 'inherit' | 'enabled' | 'disabled';
};

type RpaMcpToolLike = {
  id: string;
};

export type UnifiedMcpItem<Server extends McpServerLike, RpaTool extends RpaMcpToolLike> =
  | { kind: 'server'; id: string; server: Server }
  | { kind: 'rpa_tool'; id: string; tool: RpaTool };

type McpEndpointLike = {
  url?: string;
  command?: string;
  args?: string[];
};

type McpServerEndpointInput = {
  transport?: string;
  endpoint_config?: McpEndpointLike | null;
};

type McpToolMetaLike = {
  [key: string]: unknown;
  source?: string;
  mcp?: {
    [key: string]: unknown;
    source?: string;
    server_name?: string;
    tool_name?: string;
  };
};

type McpToolDisplayInput = {
  functionName?: string;
  fallbackName?: string;
  meta?: McpToolMetaLike | null;
};

export function groupMcpServers<T extends McpServerLike>(servers: T[]) {
  return {
    system: servers.filter((server) => server.scope === 'system'),
    user: servers.filter((server) => server.scope === 'user'),
  };
}

export function buildUnifiedMcpItems<Server extends McpServerLike, RpaTool extends RpaMcpToolLike>(
  servers: Server[],
  rpaTools: RpaTool[],
): Array<UnifiedMcpItem<Server, RpaTool>> {
  return [
    ...servers.map((server) => ({
      kind: 'server' as const,
      id: server.id || server.server_key || '',
      server,
    })),
    ...rpaTools.map((tool) => ({
      kind: 'rpa_tool' as const,
      id: tool.id,
      tool,
    })),
  ];
}

export function isRpaGatewayServer(server?: McpServerLike | null): boolean {
  return Boolean(
    server
      && server.scope === 'system'
      && (server.id === 'rpa_gateway' || server.server_key === 'system:rpa_gateway'),
  );
}

export function formatMcpServerDescription(server: McpServerLike, t: (key: string) => string): string {
  if (isRpaGatewayServer(server)) {
    return t('RPA gateway platform description');
  }
  return server.description || t('No description');
}

export function computeEffectiveMcpEnabled(server: McpServerLike): boolean {
  if (!server.enabled) {
    return false;
  }
  if (server.session_mode === 'enabled') {
    return true;
  }
  if (server.session_mode === 'disabled') {
    return false;
  }
  return Boolean(server.default_enabled);
}

export function isMcpToolMeta(meta?: McpToolMetaLike | null): boolean {
  if (!meta) {
    return false;
  }
  return meta.source === 'mcp' || meta.mcp?.source === 'mcp';
}

export function parseHttpHeaderText(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const separatorIndex = line.indexOf(':');
    if (separatorIndex <= 0) {
      continue;
    }
    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim();
    if (key && value) {
      headers[key] = value;
    }
  }
  return headers;
}

export function stringifyHttpHeaders(headers?: Record<string, string> | null): string {
  if (!headers) {
    return '';
  }
  return Object.entries(headers)
    .filter(([key, value]) => key.trim() && value.trim())
    .map(([key, value]) => `${key.trim()}: ${value.trim()}`)
    .join('\n');
}

export function parseKeyValueTemplateText(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }

    const equalsIndex = line.indexOf('=');
    const colonIndex = line.indexOf(':');
    const separatorIndex = equalsIndex >= 0 && (colonIndex < 0 || equalsIndex < colonIndex)
      ? equalsIndex
      : colonIndex;
    if (separatorIndex <= 0) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim();
    if (key && value) {
      values[key] = value;
    }
  }
  return values;
}

export function stringifyKeyValueTemplateMap(values?: Record<string, string> | null): string {
  if (!values) {
    return '';
  }
  return Object.entries(values)
    .filter(([key, value]) => key.trim() && value.trim())
    .map(([key, value]) => `${key.trim()}=${value.trim()}`)
    .join('\n');
}

export function hasCredentialTemplate(value: string): boolean {
  return /{{\s*[A-Za-z_][\w-]*\.(password|username|domain)\s*}}/.test(value);
}

export function splitCredentialTemplateMap(values: Record<string, string>): {
  staticValues: Record<string, string>;
  credentialValues: Record<string, string>;
} {
  const staticValues: Record<string, string> = {};
  const credentialValues: Record<string, string> = {};

  for (const [key, value] of Object.entries(values)) {
    if (hasCredentialTemplate(value)) {
      credentialValues[key] = value;
    } else {
      staticValues[key] = value;
    }
  }

  return { staticValues, credentialValues };
}

export function formatMcpToolDisplayName(input: McpToolDisplayInput): string {
  const mcpMeta = input.meta?.mcp;
  const serverName = typeof mcpMeta?.server_name === 'string' ? mcpMeta.server_name.trim() : '';
  const toolName = typeof mcpMeta?.tool_name === 'string' ? mcpMeta.tool_name.trim() : '';

  if (serverName && toolName) {
    return `${serverName} / ${toolName}`;
  }
  if (toolName) {
    return toolName;
  }
  if (serverName) {
    return serverName;
  }
  return input.functionName || input.fallbackName || '';
}

export function formatMcpServerEndpoint(server: McpServerEndpointInput): string {
  const endpoint = server.endpoint_config || {};
  if (server.transport === 'stdio') {
    const command = endpoint.command?.trim();
    const args = (endpoint.args || []).map((arg) => arg.trim()).filter(Boolean);
    return [command, ...args].filter(Boolean).join(' ') || 'stdio';
  }
  return endpoint.url?.trim() || 'No endpoint';
}

import { describe, expect, it } from 'vitest';

import {
  computeEffectiveMcpEnabled,
  buildUnifiedMcpItems,
  formatMcpServerDescription,
  groupMcpServers,
  hasCredentialTemplate,
  isMcpToolMeta,
  parseKeyValueTemplateText,
  parseHttpHeaderText,
  splitCredentialTemplateMap,
  formatMcpServerEndpoint,
  formatMcpToolDisplayName,
  stringifyKeyValueTemplateMap,
  stringifyHttpHeaders,
} from './mcpUi';

describe('groupMcpServers', () => {
  it('splits system and user servers while preserving order', () => {
    const result = groupMcpServers([
      { server_key: 'system:pubmed', scope: 'system', name: 'PubMed' },
      { server_key: 'user:notion', scope: 'user', name: 'Notion' },
      { server_key: 'system:arxiv', scope: 'system', name: 'Arxiv' },
    ]);

    expect(result.system.map((server) => server.server_key)).toEqual([
      'system:pubmed',
      'system:arxiv',
    ]);
    expect(result.user.map((server) => server.server_key)).toEqual([
      'user:notion',
    ]);
  });
});

describe('buildUnifiedMcpItems', () => {
  it('merges user MCP servers and RPA MCP tools into one render list', () => {
    const items = buildUnifiedMcpItems(
      [
        { id: 'server_1', server_key: 'user:notion', name: 'Notion' },
        { id: 'server_2', server_key: 'user:github', name: 'GitHub' },
      ],
      [
        { id: 'rpa_mcp_1', tool_name: 'fetch_issue', name: 'Fetch issue' },
      ],
    );

    expect(items).toEqual([
      { kind: 'server', id: 'server_1', server: { id: 'server_1', server_key: 'user:notion', name: 'Notion' } },
      { kind: 'server', id: 'server_2', server: { id: 'server_2', server_key: 'user:github', name: 'GitHub' } },
      { kind: 'rpa_tool', id: 'rpa_mcp_1', tool: { id: 'rpa_mcp_1', tool_name: 'fetch_issue', name: 'Fetch issue' } },
    ]);
  });
});

describe('formatMcpServerDescription', () => {
  it('explains the internal RPA gateway in user-facing language', () => {
    expect(
      formatMcpServerDescription(
        {
          id: 'rpa_gateway',
          server_key: 'system:rpa_gateway',
          scope: 'system',
          description: 'Converted RPA tools exposed through one central gateway',
        },
        (key) => `translated:${key}`,
      ),
    ).toBe('translated:RPA gateway platform description');
  });

  it('uses the configured description for normal MCP servers', () => {
    expect(
      formatMcpServerDescription(
        { id: 'pubmed', scope: 'system', description: 'PubMed search access' },
        (key) => key,
      ),
    ).toBe('PubMed search access');
  });
});

describe('computeEffectiveMcpEnabled', () => {
  it('uses explicit session overrides ahead of defaults', () => {
    expect(computeEffectiveMcpEnabled({ enabled: true, default_enabled: false, session_mode: 'enabled' })).toBe(true);
    expect(computeEffectiveMcpEnabled({ enabled: true, default_enabled: true, session_mode: 'disabled' })).toBe(false);
  });

  it('falls back to default enablement when session mode inherits', () => {
    expect(computeEffectiveMcpEnabled({ enabled: true, default_enabled: true, session_mode: 'inherit' })).toBe(true);
    expect(computeEffectiveMcpEnabled({ enabled: true, default_enabled: false, session_mode: 'inherit' })).toBe(false);
    expect(computeEffectiveMcpEnabled({ enabled: false, default_enabled: true, session_mode: 'enabled' })).toBe(false);
  });
});

describe('isMcpToolMeta', () => {
  it('detects MCP tools from nested mcp metadata', () => {
    expect(
      isMcpToolMeta({
        icon: 'x',
        category: 'custom',
        description: 'demo',
        mcp: {
          source: 'mcp',
          server_name: 'PubMed',
          tool_name: 'search_articles',
        },
      }),
    ).toBe(true);
  });

  it('does not classify sandbox tools as MCP tools', () => {
    expect(
      isMcpToolMeta({
        icon: 'x',
        category: 'execution',
        description: 'demo',
        sandbox: true,
      }),
    ).toBe(false);
  });
});

describe('HTTP header text helpers', () => {
  it('parses one header per line with colon separators', () => {
    expect(
      parseHttpHeaderText(`
Authorization: Bearer token
X-Api-Key: abc:123
      `),
    ).toEqual({
      Authorization: 'Bearer token',
      'X-Api-Key': 'abc:123',
    });
  });

  it('skips blank lines and invalid rows', () => {
    expect(parseHttpHeaderText('\nInvalid\nAccept: application/json\n')).toEqual({
      Accept: 'application/json',
    });
  });

  it('stringifies headers back to editable text', () => {
    expect(stringifyHttpHeaders({ Authorization: 'Bearer token', Accept: 'application/json' })).toBe(
      'Authorization: Bearer token\nAccept: application/json',
    );
  });
});

describe('key/value template text helpers', () => {
  it('parses equals and colon separators while preserving template values', () => {
    expect(
      parseKeyValueTemplateText(`
GITHUB_TOKEN={{ github.password }}
SENTRY_TOKEN: {{ sentry.password }}
      `),
    ).toEqual({
      GITHUB_TOKEN: '{{ github.password }}',
      SENTRY_TOKEN: '{{ sentry.password }}',
    });
  });

  it('stringifies template maps using equals separators', () => {
    expect(
      stringifyKeyValueTemplateMap({
        GITHUB_TOKEN: '{{ github.password }}',
        SENTRY_TOKEN: '{{ sentry.password }}',
      }),
    ).toBe('GITHUB_TOKEN={{ github.password }}\nSENTRY_TOKEN={{ sentry.password }}');
  });
});

describe('credential template map helpers', () => {
  it('detects credential placeholders in values', () => {
    expect(hasCredentialTemplate('Bearer {{ github.password }}')).toBe(true);
    expect(hasCredentialTemplate('application/json')).toBe(false);
  });

  it('splits static values from credential template values', () => {
    expect(
      splitCredentialTemplateMap({
        Accept: 'application/json',
        Authorization: 'Bearer {{ github.password }}',
        'X-Client': 'RpaClaw',
      }),
    ).toEqual({
      staticValues: {
        Accept: 'application/json',
        'X-Client': 'RpaClaw',
      },
      credentialValues: {
        Authorization: 'Bearer {{ github.password }}',
      },
    });
  });
});

describe('formatMcpToolDisplayName', () => {
  it('uses MCP server name and tool name from metadata', () => {
    expect(
      formatMcpToolDisplayName({
        functionName: 'mcp__mcp_8cf94a8a265a__resolve-library-id',
        fallbackName: 'mcp__mcp_8cf94a8a265a__resolve-library-id',
        meta: {
          mcp: {
            source: 'mcp',
            server_name: 'Context7',
            tool_name: 'resolve-library-id',
          },
        },
      }),
    ).toBe('Context7 / resolve-library-id');
  });

  it('falls back to the MCP tool name when server metadata is incomplete', () => {
    expect(
      formatMcpToolDisplayName({
        functionName: 'mcp__mcp_8cf94a8a265a__resolve-library-id',
        fallbackName: 'mcp__mcp_8cf94a8a265a__resolve-library-id',
        meta: {
          mcp: {
            source: 'mcp',
            tool_name: 'resolve-library-id',
          },
        },
      }),
    ).toBe('resolve-library-id');
  });
});

describe('formatMcpServerEndpoint', () => {
  it('shows the command for stdio MCP servers', () => {
    expect(
      formatMcpServerEndpoint({
        transport: 'stdio',
        endpoint_config: {
          command: 'npx',
          args: ['-y', '@modelcontextprotocol/server-filesystem'],
        },
      }),
    ).toBe('npx -y @modelcontextprotocol/server-filesystem');
  });

  it('shows the URL for remote MCP servers', () => {
    expect(
      formatMcpServerEndpoint({
        transport: 'streamable_http',
        endpoint_config: {
          url: 'https://mcp.example.com/mcp',
        },
      }),
    ).toBe('https://mcp.example.com/mcp');
  });

  it('falls back to a readable label when endpoint data is missing', () => {
    expect(
      formatMcpServerEndpoint({
        transport: 'sse',
        endpoint_config: {},
      }),
    ).toBe('No endpoint');
  });
});

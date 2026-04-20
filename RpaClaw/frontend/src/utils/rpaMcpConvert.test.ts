import { describe, expect, it, vi } from 'vitest';

import {
  buildRpaRecorderLocation,
  buildRpaToolEditorLocation,
  buildPreviewDraftSignature,
  focusPreviewTestSection,
  getPreviewTestStatus,
  hasMatchingPreviewTest,
} from './rpaMcpConvert';

describe('buildRpaToolEditorLocation', () => {
  it('routes MCP publishing into the Tools domain with session context', () => {
    expect(buildRpaToolEditorLocation({
      sessionId: 'session-1',
      skillName: 'github-project-issue',
      skillDescription: 'Fetch the first issue',
    })).toEqual({
      path: '/chat/tools/mcp/new',
      query: {
        source: 'rpa-session',
        sessionId: 'session-1',
        skillName: 'github-project-issue',
        skillDescription: 'Fetch the first issue',
      },
    });
  });
});

describe('buildRpaRecorderLocation', () => {
  it('routes Tool Studio users into the recorder with MCP creation intent', () => {
    expect(buildRpaRecorderLocation()).toEqual({
      path: '/rpa/recorder',
      query: {
        source: 'mcp-tool-studio',
      },
    });
  });
});

describe('getPreviewTestStatus', () => {
  it('returns untested before any preview test runs', () => {
    expect(getPreviewTestStatus({
      hasMatchingSuccessfulTest: false,
      testResult: null,
      hasConfigChangesSinceLastTest: false,
    })).toBe('untested');
  });

  it('returns failed after an unsuccessful preview test', () => {
    expect(getPreviewTestStatus({
      hasMatchingSuccessfulTest: false,
      testResult: { success: false } as any,
      hasConfigChangesSinceLastTest: false,
    })).toBe('failed');
  });

  it('returns success after a successful preview test', () => {
    expect(getPreviewTestStatus({
      hasMatchingSuccessfulTest: true,
      testResult: { success: true } as any,
      hasConfigChangesSinceLastTest: false,
    })).toBe('success');
  });

  it('returns stale when config changed after a successful preview test', () => {
    expect(getPreviewTestStatus({
      hasMatchingSuccessfulTest: false,
      testResult: { success: true } as any,
      hasConfigChangesSinceLastTest: true,
    })).toBe('stale');
  });
});

describe('buildPreviewDraftSignature', () => {
  it('normalizes execution-relevant values into a stable signature', () => {
    expect(buildPreviewDraftSignature({
      sessionId: 's1',
      name: ' tool ',
      description: ' desc ',
      allowedDomains: [' github.com ', '', 'api.github.com'],
      postAuthStartUrl: ' https://github.com/trending ',
    })).toBe(
      JSON.stringify({
        session_id: 's1',
        allowed_domains: ['github.com', 'api.github.com'],
        post_auth_start_url: 'https://github.com/trending',
      }),
    );
  });

  it('does not change when only name or description changes', () => {
    expect(buildPreviewDraftSignature({
      sessionId: 's1',
      name: 'tool-a',
      description: 'desc-a',
      allowedDomains: ['github.com'],
      postAuthStartUrl: 'https://github.com/trending',
    })).toBe(buildPreviewDraftSignature({
      sessionId: 's1',
      name: 'tool-b',
      description: 'desc-b',
      allowedDomains: ['github.com'],
      postAuthStartUrl: 'https://github.com/trending',
    }));
  });
});

describe('hasMatchingPreviewTest', () => {
  it('requires current and tested signatures to match', () => {
    expect(hasMatchingPreviewTest('a', 'a')).toBe(true);
    expect(hasMatchingPreviewTest('a', 'b')).toBe(false);
    expect(hasMatchingPreviewTest('a', null)).toBe(false);
  });
});

describe('focusPreviewTestSection', () => {
  it('scrolls the preview test section into view and focuses its primary action', () => {
    const focus = vi.fn();
    const querySelector = vi.fn(() => ({ focus }));
    const scrollIntoView = vi.fn();

    focusPreviewTestSection({
      scrollIntoView,
      querySelector,
    } as unknown as HTMLElement);

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(querySelector).toHaveBeenCalledWith('[data-preview-test-action]');
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it('does nothing when the section is missing', () => {
    expect(() => focusPreviewTestSection(null)).not.toThrow();
  });
});

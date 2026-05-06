// @vitest-environment jsdom

import { describe, expect, it, beforeEach } from 'vitest';
import { buildRpaAssistantRequestHeaders } from './rpaAssistantRequest';

describe('buildRpaAssistantRequestHeaders', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('uses the stored access token for RPA assistant requests', () => {
    localStorage.setItem('access_token', 'access-token-1');
    localStorage.setItem('token', 'legacy-token');

    expect(buildRpaAssistantRequestHeaders()).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer access-token-1',
    });
  });
});

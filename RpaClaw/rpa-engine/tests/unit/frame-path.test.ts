import { describe, expect, it } from 'vitest';
import { joinFramePath } from '../../src/playwright/frame-path.js';

describe('joinFramePath', () => {
  it('preserves nested frame selector order', () => {
    expect(joinFramePath(['iframe[name="shell"]', 'iframe[title="editor"]'])).toEqual([
      'iframe[name="shell"]',
      'iframe[title="editor"]',
    ]);
  });
});

import { describe, expect, it } from 'vitest';
import { SessionRegistry } from '../../src/session-registry.js';

describe('SessionRegistry', () => {
  it('stores and retrieves runtime sessions by id', () => {
    const registry = new SessionRegistry();
    registry.set({ id: 'session-1', userId: 'u1', mode: 'idle' });
    expect(registry.get('session-1')).toMatchObject({ userId: 'u1', mode: 'idle' });
  });
});

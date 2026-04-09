import { randomUUID } from 'node:crypto';
import type { RuntimeSession } from '../contracts.js';

export function createRuntimeSession(input: {
  userId: string;
  id?: string;
  mode?: RuntimeSession['mode'];
}): RuntimeSession {
  return {
    id: input.id ?? randomUUID(),
    userId: input.userId,
    mode: input.mode ?? 'idle',
  };
}

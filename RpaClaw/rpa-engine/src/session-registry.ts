import type { RuntimeSession } from './contracts.js';

export class SessionRegistry {
  #sessions = new Map<string, RuntimeSession>();

  set(session: RuntimeSession): void {
    this.#sessions.set(session.id, session);
  }

  get(sessionId: string): RuntimeSession | undefined {
    return this.#sessions.get(sessionId);
  }

  delete(sessionId: string): void {
    this.#sessions.delete(sessionId);
  }
}

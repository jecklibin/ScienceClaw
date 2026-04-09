import type { RecordedAction } from './action-model.js';
import type { RuntimeSession } from './contracts.js';

export type EngineEventMap = {
  'action.recorded': RecordedAction;
  'session.created': RuntimeSession;
  'session.updated': RuntimeSession;
};

type Handler<T> = (event: T) => void;

export class EventBus<TEvents extends Record<string, unknown> = EngineEventMap> {
  #listeners = new Map<keyof TEvents, Set<Handler<TEvents[keyof TEvents]>>>();

  subscribe<TKey extends keyof TEvents>(
    eventName: TKey,
    handler: Handler<TEvents[TKey]>,
  ): () => void {
    const listeners =
      this.#listeners.get(eventName) ?? new Set<Handler<TEvents[keyof TEvents]>>();
    listeners.add(handler as Handler<TEvents[keyof TEvents]>);
    this.#listeners.set(eventName, listeners);

    return () => {
      listeners.delete(handler as Handler<TEvents[keyof TEvents]>);
      if (listeners.size === 0) {
        this.#listeners.delete(eventName);
      }
    };
  }

  publish<TKey extends keyof TEvents>(eventName: TKey, payload: TEvents[TKey]): void {
    const listeners = this.#listeners.get(eventName);
    if (!listeners) {
      return;
    }

    for (const listener of listeners) {
      listener(payload as TEvents[keyof TEvents]);
    }
  }
}

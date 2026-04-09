import { normalizeAction, type RecordedAction } from '../action-model.js';
import type { EventBus } from '../event-bus.js';

export class RecorderAdapter {
  constructor(private readonly eventBus: EventBus) {}

  record(
    action: Partial<RecordedAction> &
      Pick<RecordedAction, 'kind' | 'pageAlias' | 'framePath' | 'locator'>,
  ): RecordedAction {
    const normalized = normalizeAction(action);
    this.eventBus.publish('action.recorded', normalized);
    return normalized;
  }
}

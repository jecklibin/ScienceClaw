export type ActionKind =
  | 'openPage'
  | 'navigate'
  | 'click'
  | 'fill'
  | 'press'
  | 'selectOption'
  | 'check'
  | 'uncheck'
  | 'closePage';

export type LocatorDescriptor = {
  selector: string;
  locatorAst: Record<string, unknown>;
};

export type LocatorCandidate = LocatorDescriptor & {
  score: number;
  matchCount: number;
  visibleMatchCount: number;
  isSelected: boolean;
  engine: 'playwright';
  reason: string;
};

export type RecordedAction = {
  id: string;
  sessionId: string;
  seq: number;
  kind: ActionKind;
  pageAlias: string;
  framePath: string[];
  locator: LocatorDescriptor;
  locatorAlternatives: LocatorCandidate[];
  signals: Record<string, unknown>;
  input: Record<string, unknown>;
  timing: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  status: 'recorded' | 'updated' | 'replayed' | 'failed';
};

export function normalizeAction(
  input: Partial<RecordedAction> &
    Pick<RecordedAction, 'kind' | 'pageAlias' | 'framePath' | 'locator'>,
): RecordedAction {
  return {
    id: input.id ?? 'pending',
    sessionId: input.sessionId ?? 'pending',
    seq: input.seq ?? 0,
    kind: input.kind,
    pageAlias: input.pageAlias,
    framePath: [...input.framePath],
    locator: {
      selector: input.locator.selector,
      locatorAst: input.locator.locatorAst,
    },
    locatorAlternatives: input.locatorAlternatives ?? [],
    signals: input.signals ?? {},
    input: input.input ?? {},
    timing: input.timing ?? {},
    snapshot: input.snapshot ?? {},
    status: input.status ?? 'recorded',
  };
}

export type RuntimeSessionMode = 'idle' | 'recording' | 'replaying' | 'stopped';

export type RuntimePage = {
  alias: string;
  title: string;
  url: string;
  openerPageAlias: string | null;
  status: 'open' | 'closed';
};

export type RuntimeAction = Record<string, unknown>;

export type RuntimeSession = {
  id: string;
  userId: string;
  mode: RuntimeSessionMode;
  status: RuntimeSessionMode;
  sandboxSessionId: string;
  activePageAlias: string | null;
  pages: RuntimePage[];
  actions: RuntimeAction[];
};

export type RuntimeReplayResult = {
  success: boolean;
  output: string;
  error?: string;
  data?: Record<string, unknown>;
};

export interface SessionRuntimeController {
  startSession(session: RuntimeSession): Promise<void>;
  activatePage(session: RuntimeSession, pageAlias: string): Promise<void>;
  navigate(session: RuntimeSession, url: string, pageAlias?: string): Promise<void>;
  replay(
    session: RuntimeSession,
    actions: RuntimeAction[],
    params: Record<string, unknown>,
  ): Promise<RuntimeReplayResult>;
  stopSession(sessionId: string): Promise<void>;
}

import { describe, expect, it, vi } from 'vitest';
import { buildApp } from '../../src/app.js';
import { EventBus } from '../../src/event-bus.js';

function createRuntimeController() {
  return {
    startSession: vi.fn(async session => {
      session.activePageAlias = 'page';
      session.pages = [
        {
          alias: 'page',
          title: '',
          url: 'about:blank',
          openerPageAlias: null,
          status: 'open',
        },
      ];
    }),
    activatePage: vi.fn(async (session, pageAlias: string) => {
      const existing = session.pages.find((page: { alias: string }) => page.alias === pageAlias);
      if (existing) {
        existing.title = '';
        existing.url = 'about:blank';
        existing.openerPageAlias = null;
        existing.status = 'open';
      } else {
        session.pages.push({
          alias: pageAlias,
          title: '',
          url: 'about:blank',
          openerPageAlias: null,
          status: 'open',
        });
      }
      session.activePageAlias = pageAlias;
    }),
    navigate: vi.fn(async (session, url: string, pageAlias?: string) => {
      const alias = pageAlias ?? session.activePageAlias ?? 'page';
      const page = session.pages.find((candidate: { alias: string }) => candidate.alias === alias);
      if (page) {
        page.url = url;
      }
      session.activePageAlias = alias;
    }),
    replay: vi.fn(async () => ({
      success: false,
      output: 'SKILL_ERROR: missing replay stub',
      error: 'missing replay stub',
      data: {},
    })),
    stopSession: vi.fn(async () => undefined),
  };
}

describe('engine health endpoint', () => {
  it('returns the service name and mode', async () => {
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController: createRuntimeController() },
    );
    const response = await app.inject({ method: 'GET', url: '/health' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: 'ok',
      service: 'rpa-engine',
    });
  });

  it('creates and retrieves runtime sessions', async () => {
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController: createRuntimeController() },
    );

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });

    expect(createResponse.statusCode).toBe(200);
    const created = createResponse.json().session as {
      id: string;
      userId: string;
      mode: string;
      status: string;
      sandboxSessionId: string;
      activePageAlias: string | null;
      pages: unknown[];
      actions: unknown[];
    };
    expect(created.userId).toBe('u1');
    expect(created.mode).toBe('idle');
    expect(created.status).toBe('idle');
    expect(created.sandboxSessionId).toBe('sandbox-1');
    expect(created.activePageAlias).toBe('page');
    expect(created.pages).toEqual([
      {
        alias: 'page',
        title: '',
        url: 'about:blank',
        openerPageAlias: null,
        status: 'open',
      },
    ]);
    expect(created.actions).toEqual([]);

    const getResponse = await app.inject({
      method: 'GET',
      url: `/sessions/${created.id}`,
    });

    expect(getResponse.statusCode).toBe(200);
    expect(getResponse.json()).toEqual({
      session: created,
    });
  });

  it('activates tabs, navigates, and stops through session control endpoints', async () => {
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController: createRuntimeController() },
    );

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });
    const created = createResponse.json().session as {
      id: string;
      activePageAlias: string | null;
      pages: Array<{ alias: string; url: string }>;
      mode: string;
      status: string;
    };

    const activateResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/activate`,
      payload: { pageAlias: 'page-1' },
    });

    expect(activateResponse.statusCode).toBe(200);
    expect(activateResponse.json().session).toMatchObject({
      id: created.id,
      activePageAlias: 'page-1',
      pages: [
        { alias: 'page', url: 'about:blank' },
        { alias: 'page-1', url: 'about:blank' },
      ],
    });

    const navigateResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/navigate`,
      payload: { url: 'docs.example.com' },
    });

    expect(navigateResponse.statusCode).toBe(200);
    expect(navigateResponse.json().session).toMatchObject({
      id: created.id,
      activePageAlias: 'page-1',
      pages: [
        { alias: 'page', url: 'about:blank' },
        { alias: 'page-1', url: 'https://docs.example.com' },
      ],
    });

    const stopResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/stop`,
    });

    expect(stopResponse.statusCode).toBe(200);
    expect(stopResponse.json().session).toMatchObject({
      id: created.id,
      mode: 'stopped',
      status: 'stopped',
    });
  });

  it('rejects missing session payloads', async () => {
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController: createRuntimeController() },
    );

    const response = await app.inject({
      method: 'POST',
      url: '/sessions',
    });

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: 'body must be an object with a non-empty userId',
    });
  });

  it('rejects invalid user ids when creating sessions', async () => {
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController: createRuntimeController() },
    );

    const response = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: '' },
    });

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: 'body must be an object with a non-empty userId',
    });
  });

  it('initializes and stops a live runtime session through the controller', async () => {
    const runtimeController = createRuntimeController();

    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController },
    );

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });

    expect(createResponse.statusCode).toBe(200);
    expect(runtimeController.startSession).toHaveBeenCalledTimes(1);
    expect(createResponse.json().session).toMatchObject({
      activePageAlias: 'page',
      pages: [{ alias: 'page', url: 'about:blank' }],
    });

    const stopResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${createResponse.json().session.id}/stop`,
    });

    expect(stopResponse.statusCode).toBe(200);
    expect(runtimeController.stopSession).toHaveBeenCalledWith(createResponse.json().session.id);
  });

  it('replays actions against the runtime controller and only succeeds after execution', async () => {
    const runtimeController = createRuntimeController();
    runtimeController.replay.mockImplementation(async (_session, actions, params) => ({
      success: true,
      output: `executed ${actions.length} action(s) with ${params.secret}`,
      data: { ok: true },
    }));

    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 },
      { runtimeController },
    );

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });
    const sessionId = createResponse.json().session.id as string;
    const session = app.sessionRegistry.get(sessionId);
    if (!session) {
      throw new Error('expected session to exist');
    }
    session.actions = [
      {
        id: 'action-1',
        sessionId,
        seq: 1,
        kind: 'click',
        pageAlias: 'page',
        framePath: [],
        locator: {
          selector: 'internal:role=button[name="Save"]',
          locatorAst: { kind: 'role', role: 'button', name: 'Save' },
        },
        locatorAlternatives: [],
        signals: {},
        input: {},
        timing: {},
        snapshot: {},
        status: 'recorded',
      },
    ];

    const replayResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${sessionId}/replay`,
      payload: { params: { secret: 'resolved-secret' } },
    });

    expect(replayResponse.statusCode).toBe(200);
    expect(runtimeController.replay).toHaveBeenCalledTimes(1);
    expect(replayResponse.json().result).toMatchObject({
      success: true,
      output: 'executed 1 action(s) with resolved-secret',
    });
  });
});

describe('EventBus', () => {
  it('continues publishing when one subscriber throws', () => {
    const bus = new EventBus<{ event: { value: number } }>();
    const received: number[] = [];

    bus.subscribe('event', () => {
      throw new Error('listener failed');
    });
    bus.subscribe('event', payload => {
      received.push(payload.value);
    });

    expect(() => bus.publish('event', { value: 7 })).not.toThrow();
    expect(received).toEqual([7]);
  });
});

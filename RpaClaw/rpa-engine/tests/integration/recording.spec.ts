import { once } from 'node:events';
import { AddressInfo } from 'node:net';
import { WebSocket } from 'ws';
import { describe, expect, it } from 'vitest';
import { buildApp } from '../../src/app.js';
import type { RuntimeSession } from '../../src/contracts.js';
import { PlaywrightSessionRuntimeController } from '../../src/playwright/runtime-controller.js';
import { createRuntimeSession } from '../../src/playwright/runtime-session.js';

class FakeLocator {
  interactions: string[];
  path: string[];

  constructor(interactions: string[], path: string[] = []) {
    this.interactions = interactions;
    this.path = path;
  }

  async click() {
    this.interactions.push(`click:${this.path.join(' > ')}`);
  }

  async fill(value: string) {
    this.interactions.push(`fill:${this.path.join(' > ')}=${value}`);
  }

  async press(value: string) {
    this.interactions.push(`press:${this.path.join(' > ')}=${value}`);
  }

  async selectOption(value: string) {
    this.interactions.push(`select:${this.path.join(' > ')}=${value}`);
  }

  async check() {
    this.interactions.push(`check:${this.path.join(' > ')}`);
  }

  async uncheck() {
    this.interactions.push(`uncheck:${this.path.join(' > ')}`);
  }

  locator(selector: string) {
    return new FakeLocator(this.interactions, [...this.path, `locator:${selector}`]);
  }

  getByRole(role: string, options?: Record<string, unknown>) {
    const name = String(options?.name ?? '');
    return new FakeLocator(this.interactions, [...this.path, `role:${role}:${name}`]);
  }

  getByTestId(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `testid:${value}`]);
  }

  getByLabel(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `label:${value}`]);
  }

  getByPlaceholder(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `placeholder:${value}`]);
  }

  getByAltText(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `alt:${value}`]);
  }

  getByTitle(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `title:${value}`]);
  }

  getByText(value: string) {
    return new FakeLocator(this.interactions, [...this.path, `text:${value}`]);
  }

  frameLocator(selector: string) {
    return new FakeLocator(this.interactions, [...this.path, `frame:${selector}`]);
  }
}

class FakePage extends FakeLocator {
  private currentUrl: string;
  private currentTitle: string;
  private popupPage: FakePage | null;
  mouse = {
    move: async (x: number, y: number) => {
      this.interactions.push(`mouse:move:${Math.round(x)},${Math.round(y)}`);
    },
    down: async (options?: { button?: string; clickCount?: number }) => {
      this.interactions.push(`mouse:down:${options?.button ?? 'left'}:${options?.clickCount ?? 0}`);
    },
    up: async (options?: { button?: string; clickCount?: number }) => {
      this.interactions.push(`mouse:up:${options?.button ?? 'left'}:${options?.clickCount ?? 0}`);
    },
    wheel: async (deltaX: number, deltaY: number) => {
      this.interactions.push(`mouse:wheel:${deltaX},${deltaY}`);
    },
  };
  keyboard = {
    down: async (key: string) => {
      this.interactions.push(`keyboard:down:${key}`);
    },
    up: async (key: string) => {
      this.interactions.push(`keyboard:up:${key}`);
    },
    press: async (key: string) => {
      this.interactions.push(`keyboard:press:${key}`);
    },
    insertText: async (text: string) => {
      this.interactions.push(`keyboard:text:${text}`);
    },
  };

  constructor(interactions: string[], title: string, url = 'about:blank', popupPage: FakePage | null = null) {
    super(interactions);
    this.currentTitle = title;
    this.currentUrl = url;
    this.popupPage = popupPage;
  }

  url() {
    return this.currentUrl;
  }

  async title() {
    return this.currentTitle;
  }

  async goto(url: string) {
    this.currentUrl = url;
    this.interactions.push(`goto:${url}`);
  }

  async waitForLoadState(_state?: string) {}

  async waitForNavigation(_options?: Record<string, unknown>) {}

  async bringToFront() {}

  async close() {}

  async waitForEvent(eventName: string) {
    if (eventName === 'popup' && this.popupPage) {
      return this.popupPage;
    }
    throw new Error(`unexpected event ${eventName}`);
  }

  async screenshot() {
    return Buffer.from('fake-jpeg-frame');
  }

  async evaluate<T>(pageFunction: () => T) {
    void pageFunction;
    return { width: 1280, height: 720 } as T;
  }
}

class FakeContext {
  bindings = new Map<string, (source: unknown, payload: string) => Promise<void>>();
  initScripts: string[] = [];

  constructor(private readonly pages: FakePage[]) {}

  async newPage() {
    const page = this.pages.shift();
    if (!page) {
      throw new Error('no fake pages remaining');
    }
    return page;
  }

  async close() {}

  async exposeBinding(name: string, callback: (source: unknown, payload: string) => Promise<void>) {
    this.bindings.set(name, callback);
  }

  async addInitScript(script: string) {
    this.initScripts.push(script);
  }

  async emitBinding(
    name: string,
    source: { page: FakePage | null; frame: unknown | null },
    payload: Record<string, unknown>,
  ) {
    const callback = this.bindings.get(name);
    if (!callback) {
      throw new Error(`binding ${name} not found`);
    }
    await callback(source, JSON.stringify(payload));
  }
}

class FakeBrowser {
  constructor(private readonly context: FakeContext) {}

  async newContext() {
    return this.context;
  }

  async close() {}
}

function createControllerHarness() {
  const interactions: string[] = [];
  const popupPage = new FakePage(interactions, 'Popup', 'https://example.com/popup');
  const rootPage = new FakePage(interactions, 'Root', 'about:blank', popupPage);
  const context = new FakeContext([rootPage]);
  const browser = new FakeBrowser(context);
  const controller = new PlaywrightSessionRuntimeController({
    async launchBrowser() {
      return browser;
    },
  });
  return { controller, interactions, context, rootPage };
}

async function connectJsonWebSocket(url: string) {
  const ws = new WebSocket(url);
  const queue: unknown[] = [];
  const waiters: Array<(message: unknown) => void> = [];

  ws.on('message', payload => {
    const message = JSON.parse(String(payload));
    const next = waiters.shift();
    if (next) {
      next(message);
      return;
    }
    queue.push(message);
  });

  await once(ws, 'open');

  const nextMessage = async () => {
    const queued = queue.shift();
    if (queued !== undefined) {
      return queued;
    }
    return await new Promise(resolve => {
      waiters.push(resolve);
    });
  };

  return { ws, nextMessage };
}

describe('PlaywrightSessionRuntimeController integration', () => {
  it('replays popup clicks inside frames against the live runtime session', async () => {
    const { controller, interactions } = createControllerHarness();
    const session: RuntimeSession = createRuntimeSession({ userId: 'u1', sandboxSessionId: 'sandbox-1' });

    await controller.startSession(session);

    const result = await controller.replay(
      session,
      [
        {
          id: 'action-1',
          sessionId: session.id,
          seq: 1,
          kind: 'click',
          pageAlias: 'page',
          framePath: ['iframe[name="editor"]'],
          locator: {
            selector: 'internal:role=button[name="Open popup"]',
            locatorAst: { kind: 'role', role: 'button', name: 'Open popup' },
          },
          locatorAlternatives: [],
          signals: { popup: { targetPageAlias: 'popup1' } },
          input: {},
          timing: {},
          snapshot: {},
          status: 'recorded',
        },
      ],
      {},
    );

    expect(result.success).toBe(true);
    expect(interactions).toContain('click:frame:iframe[name="editor"] > role:button:Open popup');
    expect(session.activePageAlias).toBe('popup1');
    expect(session.pages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ alias: 'page', status: 'open' }),
        expect.objectContaining({ alias: 'popup1', openerPageAlias: 'page', url: 'https://example.com/popup' }),
      ]),
    );
  });

  it('streams screencast frames and forwards input events over the engine websocket', async () => {
    const { controller, interactions } = createControllerHarness();
    const app = buildApp(
      { NODE_ENV: 'test', RPA_ENGINE_HOST: '127.0.0.1', RPA_ENGINE_PORT: 0 },
      { runtimeController: controller },
    );

    await app.listen({ host: '127.0.0.1', port: 0 });
    const address = app.server.address() as AddressInfo;

    try {
      const createResponse = await app.inject({
        method: 'POST',
        url: '/sessions',
        payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
      });
      const sessionId = createResponse.json().session.id as string;

      const { ws, nextMessage } = await connectJsonWebSocket(
        `ws://127.0.0.1:${address.port}/sessions/${sessionId}/screencast`,
      );

      try {
        const openingMessages = [
          await nextMessage(),
          await nextMessage(),
          await nextMessage(),
        ];
        const messageTypes = openingMessages.map(message => message.type);

        expect(messageTypes).toContain('tabs_snapshot');
        expect(messageTypes).toContain('frame');

        ws.send(
          JSON.stringify({
            type: 'mouse',
            action: 'mousePressed',
            x: 0.5,
            y: 0.25,
            button: 'left',
            clickCount: 1,
            modifiers: 0,
          }),
        );

        await new Promise(resolve => setTimeout(resolve, 25));
        expect(interactions).toContain('mouse:move:640,180');
        expect(interactions).toContain('mouse:down:left:1');
      } finally {
        ws.close();
        await once(ws, 'close');
      }
    } finally {
      await app.close();
    }
  });

  it('records browser events into session actions through the context recorder bridge', async () => {
    const { controller, context, rootPage } = createControllerHarness();
    const session: RuntimeSession = createRuntimeSession({ userId: 'u1', sandboxSessionId: 'sandbox-1' });

    await controller.startSession(session);

    await context.emitBinding(
      '__rpa_emit',
      { page: rootPage, frame: null },
      {
        action: 'click',
        locator: {
          method: 'role',
          role: 'button',
          name: 'Save',
          selector: 'internal:role=button[name="Save"]',
        },
        locator_candidates: [
          {
            kind: 'role',
            score: 100,
            strict_match_count: 1,
            visible_match_count: 1,
            selected: true,
            locator: {
              method: 'role',
              role: 'button',
              name: 'Save',
              selector: 'internal:role=button[name="Save"]',
            },
            reason: 'strict unique match',
          },
        ],
        validation: { status: 'ok' },
        frame_path: [],
        signals: {},
        element_snapshot: {
          tag: 'button',
          role: 'button',
          name: 'Save',
          text: 'Save',
          url: 'https://example.com/editor',
        },
        value: '',
        timestamp: 1000,
      },
    );

    expect(session.actions).toHaveLength(1);
    expect(session.actions[0]).toMatchObject({
      kind: 'click',
      pageAlias: 'page',
      framePath: [],
      locator: {
        selector: 'internal:role=button[name="Save"]',
      },
      locatorAlternatives: [
        expect.objectContaining({
          selector: 'internal:role=button[name="Save"]',
          isSelected: true,
        }),
      ],
      snapshot: expect.objectContaining({
        tag: 'button',
        role: 'button',
        name: 'Save',
        url: 'https://example.com/editor',
      }),
      status: 'recorded',
    });
  });

  it('records explicit session navigations as actions for backend compatibility polling', async () => {
    const { controller } = createControllerHarness();
    const session: RuntimeSession = createRuntimeSession({ userId: 'u1', sandboxSessionId: 'sandbox-1' });

    await controller.startSession(session);
    await controller.navigate(session, 'https://docs.example.com', 'page');

    expect(session.actions).toHaveLength(1);
    expect(session.actions[0]).toMatchObject({
      kind: 'navigate',
      pageAlias: 'page',
      input: {
        url: 'https://docs.example.com',
      },
      snapshot: {
        url: 'https://docs.example.com',
      },
      status: 'recorded',
    });
  });
});

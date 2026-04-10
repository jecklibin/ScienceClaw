import { describe, expect, it } from 'vitest';
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
}

class FakeContext {
  constructor(private readonly pages: FakePage[]) {}

  async newPage() {
    const page = this.pages.shift();
    if (!page) {
      throw new Error('no fake pages remaining');
    }
    return page;
  }

  async close() {}
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
  return { controller, interactions };
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
});

import { chromium } from 'playwright';
import type { RuntimeAction, RuntimeReplayResult, RuntimeSession, SessionRuntimeController } from '../contracts.js';
import type { RecordedAction } from '../action-model.js';
import { ensureRuntimePage } from './runtime-session.js';

type PageLike = {
  url(): string;
  title(): Promise<string>;
  goto(url: string): Promise<unknown>;
  waitForLoadState(state?: string): Promise<unknown>;
  waitForNavigation(options?: Record<string, unknown>): Promise<unknown>;
  bringToFront(): Promise<unknown>;
  close(): Promise<unknown>;
  waitForEvent(eventName: string): Promise<unknown>;
  locator(selector: string): LocatorLike;
  getByRole(role: string, options?: Record<string, unknown>): LocatorLike;
  getByTestId(value: string): LocatorLike;
  getByLabel(value: string, options?: Record<string, unknown>): LocatorLike;
  getByPlaceholder(value: string, options?: Record<string, unknown>): LocatorLike;
  getByAltText(value: string, options?: Record<string, unknown>): LocatorLike;
  getByTitle(value: string, options?: Record<string, unknown>): LocatorLike;
  getByText(value: string, options?: Record<string, unknown>): LocatorLike;
  frameLocator(selector: string): FrameScopeLike;
  screenshot?(options?: Record<string, unknown>): Promise<Uint8Array>;
  evaluate?<T>(pageFunction: () => T): Promise<T>;
  mouse?: {
    move(x: number, y: number): Promise<unknown>;
    down(options?: Record<string, unknown>): Promise<unknown>;
    up(options?: Record<string, unknown>): Promise<unknown>;
    wheel(deltaX: number, deltaY: number): Promise<unknown>;
  };
  keyboard?: {
    down(key: string): Promise<unknown>;
    up(key: string): Promise<unknown>;
    press(key: string): Promise<unknown>;
    insertText(text: string): Promise<unknown>;
  };
};

type LocatorLike = {
  click(): Promise<unknown>;
  fill(value: string): Promise<unknown>;
  press(value: string): Promise<unknown>;
  selectOption(value: string): Promise<unknown>;
  check(): Promise<unknown>;
  uncheck(): Promise<unknown>;
  locator(selector: string): LocatorLike;
  getByRole(role: string, options?: Record<string, unknown>): LocatorLike;
  getByTestId(value: string): LocatorLike;
  getByLabel(value: string, options?: Record<string, unknown>): LocatorLike;
  getByPlaceholder(value: string, options?: Record<string, unknown>): LocatorLike;
  getByAltText(value: string, options?: Record<string, unknown>): LocatorLike;
  getByTitle(value: string, options?: Record<string, unknown>): LocatorLike;
  getByText(value: string, options?: Record<string, unknown>): LocatorLike;
  frameLocator(selector: string): FrameScopeLike;
};

type FrameScopeLike = LocatorLike;

type ContextLike = {
  newPage(): Promise<PageLike>;
  exposeBinding?(
    name: string,
    callback: (source: { page?: PageLike; frame?: unknown }, payload: string) => Promise<void>,
  ): Promise<unknown>;
  addInitScript?(script: string): Promise<unknown>;
  close(): Promise<unknown>;
};

type BrowserLike = {
  newContext(options?: Record<string, unknown>): Promise<ContextLike>;
  close(): Promise<unknown>;
};

type DownloadLike = {
  suggestedFilename(): string;
};

type RuntimeHandles = {
  browser: BrowserLike;
  context: ContextLike;
  pages: Map<string, PageLike>;
  activePageAlias: string;
};

type RuntimeDriver = {
  launchBrowser(): Promise<BrowserLike>;
};

class PlaywrightDriver implements RuntimeDriver {
  async launchBrowser(): Promise<BrowserLike> {
    return chromium.launch({ headless: true });
  }
}

const RECORDER_INIT_SCRIPT = String.raw`
(() => {
  if (window.__rpa_engine_recorder_installed) return;
  window.__rpa_engine_recorder_installed = true;

  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const cssEscape = value => {
    try {
      return CSS.escape(String(value));
    } catch {
      return String(value).replace(/([\\"'\\[\\](){}|^$.*+?])/g, '\\$1');
    }
  };
  const roleMap = {
    BUTTON: 'button',
    A: 'link',
    SELECT: 'combobox',
    TEXTAREA: 'textbox',
    IMG: 'img',
  };

  const getRole = element => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit;
    if (element.tagName === 'INPUT') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button';
      return 'textbox';
    }
    return roleMap[element.tagName] || null;
  };

  const accessibleName = element => {
    const aria = normalize(element.getAttribute('aria-label'));
    if (aria) return aria;

    const labelledBy = normalize(element.getAttribute('aria-labelledby'));
    if (labelledBy) {
      const labels = labelledBy
        .split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(node => normalize(node.textContent))
        .filter(Boolean);
      if (labels.length) return labels.join(' ').slice(0, 80);
    }

    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)) {
      if (element.id) {
        const label = document.querySelector('label[for="' + cssEscape(element.id) + '"]');
        if (label) return normalize(label.textContent).slice(0, 80);
      }
      const parentLabel = element.closest('label');
      if (parentLabel) return normalize(parentLabel.textContent).slice(0, 80);
    }

    return normalize(element.textContent).slice(0, 80);
  };

  const retarget = element => {
    if (!(element instanceof Element)) return null;
    let current = element;
    while (current && current !== document.body) {
      if (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT', 'OPTION', 'LABEL'].includes(current.tagName)) {
        return current;
      }
      const role = current.getAttribute('role');
      if (role && ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'option', 'switch', 'combobox'].includes(role)) {
        return current;
      }
      current = current.parentElement;
    }
    return element;
  };

  const locatorFromElement = element => {
    const role = getRole(element);
    const name = accessibleName(element);
    if (role && name) {
      return {
        method: 'role',
        role,
        name,
        selector: 'internal:role=' + role + '[name="' + name.replace(/"/g, '\\"') + '"]',
      };
    }

    const testId = element.getAttribute('data-testid') || element.getAttribute('data-test-id');
    if (testId) {
      return {
        method: 'testId',
        value: testId,
        selector: '[data-testid="' + cssEscape(testId) + '"]',
      };
    }

    const placeholder = normalize(element.getAttribute('placeholder'));
    if (placeholder) {
      return {
        method: 'placeholder',
        value: placeholder,
        selector: '[placeholder="' + cssEscape(placeholder) + '"]',
      };
    }

    if (element.id) {
      return {
        method: 'css',
        value: '#' + cssEscape(element.id),
        selector: '#' + cssEscape(element.id),
      };
    }

    const nameAttr = normalize(element.getAttribute('name'));
    if (nameAttr) {
      const selector = element.tagName.toLowerCase() + '[name="' + cssEscape(nameAttr) + '"]';
      return {
        method: 'css',
        value: selector,
        selector,
      };
    }

    const selector = element.tagName.toLowerCase();
    return {
      method: 'css',
      value: selector,
      selector,
    };
  };

  const buildCandidates = locator => [{
    kind: locator.method,
    score: 100,
    strict_match_count: 1,
    visible_match_count: 1,
    selected: true,
    locator,
    reason: 'selected generated locator',
  }];

  const snapshotFromElement = element => ({
    tag: element.tagName.toLowerCase(),
    role: getRole(element) || '',
    name: accessibleName(element),
    text: normalize(element.textContent).slice(0, 120),
    id: element.id || '',
    title: normalize(element.getAttribute('title')),
    placeholder: normalize(element.getAttribute('placeholder')),
    url: location.href,
  });

  const emit = payload => {
    try {
      window.__rpa_emit(JSON.stringify(payload));
    } catch {
      // ignore recorder transport failures inside the page
    }
  };

  document.addEventListener('click', event => {
    const target = retarget(event.target);
    if (!target) return;
    const locator = locatorFromElement(target);
    emit({
      action: 'click',
      locator,
      locator_candidates: buildCandidates(locator),
      validation: { status: 'ok' },
      frame_path: [],
      signals: {},
      element_snapshot: snapshotFromElement(target),
      timestamp: Date.now(),
    });
  }, true);

  document.addEventListener('input', event => {
    const target = retarget(event.target);
    if (!target) return;
    if (!('value' in target)) return;
    const locator = locatorFromElement(target);
    emit({
      action: 'fill',
      locator,
      locator_candidates: buildCandidates(locator),
      validation: { status: 'ok' },
      frame_path: [],
      signals: {},
      element_snapshot: snapshotFromElement(target),
      value: target.value,
      timestamp: Date.now(),
    });
  }, true);

  document.addEventListener('change', event => {
    const target = retarget(event.target);
    if (!target) return;
    const locator = locatorFromElement(target);

    if (target instanceof HTMLSelectElement) {
      emit({
        action: 'selectOption',
        locator,
        locator_candidates: buildCandidates(locator),
        validation: { status: 'ok' },
        frame_path: [],
        signals: {},
        element_snapshot: snapshotFromElement(target),
        value: target.value,
        timestamp: Date.now(),
      });
      return;
    }

    if (target instanceof HTMLInputElement && (target.type === 'checkbox' || target.type === 'radio')) {
      emit({
        action: target.checked ? 'check' : 'uncheck',
        locator,
        locator_candidates: buildCandidates(locator),
        validation: { status: 'ok' },
        frame_path: [],
        signals: {},
        element_snapshot: snapshotFromElement(target),
        value: String(target.checked),
        timestamp: Date.now(),
      });
    }
  }, true);

  document.addEventListener('keydown', event => {
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      return;
    }
    const target = retarget(event.target);
    if (!target) return;
    const locator = locatorFromElement(target);
    emit({
      action: 'press',
      locator,
      locator_candidates: buildCandidates(locator),
      validation: { status: 'ok' },
      frame_path: [],
      signals: {},
      element_snapshot: snapshotFromElement(target),
      value: event.key,
      timestamp: Date.now(),
    });
  }, true);
})();
`;

export class PlaywrightSessionRuntimeController implements SessionRuntimeController {
  #driver: RuntimeDriver;
  #runtimes = new Map<string, RuntimeHandles>();

  constructor(driver: RuntimeDriver = new PlaywrightDriver()) {
    this.#driver = driver;
  }

  async startSession(session: RuntimeSession): Promise<void> {
    await this.stopSession(session.id);
    const browser = await this.#driver.launchBrowser();
    const context = await browser.newContext({ noViewport: true, acceptDownloads: true });
    const runtime: RuntimeHandles = {
      browser,
      context,
      pages: new Map(),
      activePageAlias: 'page',
    };
    await this.#configureRecorder(session, runtime);
    const page = await context.newPage();
    runtime.pages.set('page', page);
    this.#runtimes.set(session.id, runtime);
    session.activePageAlias = 'page';
    await this.#syncPageState(session, 'page', page, null);
  }

  async activatePage(session: RuntimeSession, pageAlias: string): Promise<void> {
    const runtime = this.#requireRuntime(session.id);
    const page = await this.#ensurePage(session, runtime, pageAlias);
    runtime.activePageAlias = pageAlias;
    session.activePageAlias = pageAlias;
    await page.bringToFront();
    await this.#syncPageState(session, pageAlias, page, null);
  }

  async navigate(session: RuntimeSession, url: string, pageAlias?: string): Promise<void> {
    const runtime = this.#requireRuntime(session.id);
    const alias = pageAlias ?? session.activePageAlias ?? runtime.activePageAlias ?? 'page';
    const page = await this.#ensurePage(session, runtime, alias);
    runtime.activePageAlias = alias;
    session.activePageAlias = alias;
    const normalizedUrl = normalizeUrl(url);
    await page.goto(normalizedUrl);
    await page.waitForLoadState('domcontentloaded');
    await this.#syncPageState(session, alias, page, null);
    this.#upsertRecordedAction(session, {
      id: `${session.id}-action-${session.actions.length + 1}`,
      sessionId: session.id,
      seq: session.actions.length + 1,
      kind: 'navigate',
      pageAlias: alias,
      framePath: [],
      locator: {
        selector: '',
        locatorAst: { kind: 'url' },
      },
      locatorAlternatives: [],
      signals: {},
      input: { url: normalizedUrl },
      timing: { timestamp: Date.now() },
      snapshot: { url: normalizedUrl },
      status: 'recorded',
    });
  }

  async replay(
    session: RuntimeSession,
    actions: RuntimeAction[],
    params: Record<string, unknown>,
  ): Promise<RuntimeReplayResult> {
    const runtime = this.#requireRuntime(session.id);
    const typedActions = actions as RecordedAction[];
    const results: Record<string, unknown> = {};

    try {
      let currentAlias = session.activePageAlias ?? runtime.activePageAlias ?? 'page';
      for (const action of typedActions) {
        currentAlias = await this.#executeAction(session, runtime, action, params, currentAlias, results);
      }
      runtime.activePageAlias = currentAlias;
      session.activePageAlias = currentAlias;
      return {
        success: true,
        output: 'SKILL_SUCCESS',
        data: results,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        output: `SKILL_ERROR: ${message}`,
        error: message,
        data: results,
      };
    }
  }

  async stopSession(sessionId: string): Promise<void> {
    const runtime = this.#runtimes.get(sessionId);
    if (!runtime) {
      return;
    }
    this.#runtimes.delete(sessionId);
    await runtime.context.close();
    await runtime.browser.close();
  }

  async captureFrame(session: RuntimeSession): Promise<{ data: string; metadata: { width: number; height: number } }> {
    const runtime = this.#requireRuntime(session.id);
    const alias = runtime.activePageAlias ?? session.activePageAlias ?? 'page';
    const page = await this.#ensurePage(session, runtime, alias);
    await this.#syncPageState(session, alias, page, null);

    if (!page.screenshot) {
      throw new Error('active page does not support screenshots');
    }

    const metadata = await this.#readViewport(page);
    const screenshot = await page.screenshot({ type: 'jpeg', quality: 40 });
    return {
      data: Buffer.from(screenshot).toString('base64'),
      metadata,
    };
  }

  async dispatchInput(session: RuntimeSession, payload: Record<string, unknown>): Promise<void> {
    const runtime = this.#requireRuntime(session.id);
    const alias = runtime.activePageAlias ?? session.activePageAlias ?? 'page';
    const page = await this.#ensurePage(session, runtime, alias);
    const inputType = String(payload.type ?? '');

    switch (inputType) {
      case 'mouse':
        await this.#dispatchMouse(page, payload);
        break;
      case 'wheel':
        await this.#dispatchWheel(page, payload);
        break;
      case 'keyboard':
        await this.#dispatchKeyboard(page, payload);
        break;
      default:
        break;
    }

    await this.#syncPageState(session, alias, page, null);
  }

  async #executeAction(
    session: RuntimeSession,
    runtime: RuntimeHandles,
    action: RecordedAction,
    params: Record<string, unknown>,
    currentAlias: string,
    results: Record<string, unknown>,
  ): Promise<string> {
    if (action.kind === 'openPage') {
      const targetAlias = action.pageAlias || currentAlias;
      const page = await this.#ensurePage(session, runtime, targetAlias);
      runtime.activePageAlias = targetAlias;
      session.activePageAlias = targetAlias;
      await page.bringToFront();
      await this.#syncPageState(session, targetAlias, page, null);
      return targetAlias;
    }

    if (action.kind === 'closePage') {
      const closeAlias = action.pageAlias || currentAlias;
      const page = runtime.pages.get(closeAlias);
      if (page) {
        await page.close();
        const state = ensureRuntimePage(session, closeAlias);
        state.status = 'closed';
      }
      const fallbackAlias = [...runtime.pages.keys()].find(alias => alias !== closeAlias) ?? 'page';
      runtime.activePageAlias = fallbackAlias;
      session.activePageAlias = fallbackAlias;
      return fallbackAlias;
    }

    const actionAlias = action.pageAlias || currentAlias;
    const page = await this.#ensurePage(session, runtime, actionAlias);
    runtime.activePageAlias = actionAlias;
    session.activePageAlias = actionAlias;

    if (action.kind === 'navigate') {
      const targetUrl = String(action.input.url ?? action.snapshot.url ?? '').trim();
      if (!targetUrl) {
        throw new Error(`navigate action ${action.id} is missing a url`);
      }
      await page.goto(normalizeUrl(targetUrl));
      await page.waitForLoadState('domcontentloaded');
      await this.#syncPageState(session, actionAlias, page, null);
      return actionAlias;
    }

    const scope = this.#buildScope(page, action.framePath);
    const locator = this.#buildLocator(scope, action);
    const popupSignal = asRecord(action.signals.popup);
    const navigationSignal = asRecord(action.signals.navigation);
    const downloadSignal = asRecord(action.signals.download);

    if (popupSignal) {
      const targetAlias = String(popupSignal.targetPageAlias ?? `${actionAlias}_popup`);
      const popupPromise = page.waitForEvent('popup') as Promise<PageLike>;
      await locator.click();
      const popup = await popupPromise;
      runtime.pages.set(targetAlias, popup);
      runtime.activePageAlias = targetAlias;
      session.activePageAlias = targetAlias;
      await popup.waitForLoadState('domcontentloaded');
      await this.#syncPageState(session, targetAlias, popup, actionAlias);
      return targetAlias;
    }

    if (downloadSignal) {
      const downloadPromise = page.waitForEvent('download') as Promise<DownloadLike>;
      await locator.click();
      const download = await downloadPromise;
      results.download = { filename: download.suggestedFilename() };
      await this.#syncPageState(session, actionAlias, page, null);
      return actionAlias;
    }

    if (navigationSignal) {
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
        locator.click(),
      ]);
      await this.#syncPageState(session, actionAlias, page, null);
      return actionAlias;
    }

    switch (action.kind) {
      case 'click':
        await locator.click();
        break;
      case 'fill':
        await locator.fill(resolveReplayValue(action, params));
        break;
      case 'press':
        await locator.press(String(action.input.key ?? action.input.value ?? ''));
        break;
      case 'selectOption':
        await locator.selectOption(String(action.input.value ?? ''));
        break;
      case 'check':
        await locator.check();
        break;
      case 'uncheck':
        await locator.uncheck();
        break;
      default:
        throw new Error(`unsupported action kind ${action.kind}`);
    }

    await this.#syncPageState(session, actionAlias, page, null);
    return actionAlias;
  }

  #requireRuntime(sessionId: string): RuntimeHandles {
    const runtime = this.#runtimes.get(sessionId);
    if (!runtime) {
      throw new Error(`runtime session ${sessionId} is not initialized`);
    }
    return runtime;
  }

  async #ensurePage(
    session: RuntimeSession,
    runtime: RuntimeHandles,
    pageAlias: string,
  ): Promise<PageLike> {
    const existing = runtime.pages.get(pageAlias);
    if (existing) {
      return existing;
    }

    const page = await runtime.context.newPage();
    runtime.pages.set(pageAlias, page);
    await this.#syncPageState(session, pageAlias, page, null);
    return page;
  }

  async #syncPageState(
    session: RuntimeSession,
    pageAlias: string,
    page: PageLike,
    openerPageAlias: string | null,
  ): Promise<void> {
    const state = ensureRuntimePage(session, pageAlias);
    state.url = page.url();
    state.title = await page.title();
    state.openerPageAlias = openerPageAlias;
    state.status = 'open';
  }

  #buildScope(page: PageLike, framePath: string[]): PageLike | FrameScopeLike {
    let scope: PageLike | FrameScopeLike = page;
    for (const selector of framePath) {
      scope = scope.frameLocator(selector);
    }
    return scope;
  }

  #buildLocator(scope: PageLike | FrameScopeLike, action: RecordedAction): LocatorLike {
    return buildLocator(scope, action.locator.locatorAst, action.locator.selector);
  }

  async #dispatchMouse(page: PageLike, payload: Record<string, unknown>): Promise<void> {
    if (!page.mouse) {
      throw new Error('active page does not support mouse input');
    }

    const { width, height } = await this.#readViewport(page);
    const x = clampUnitInterval(payload.x) * width;
    const y = clampUnitInterval(payload.y) * height;
    const action = String(payload.action ?? '');

    await page.mouse.move(x, y);

    if (action === 'mousePressed') {
      await page.mouse.down({
        button: String(payload.button ?? 'left'),
        clickCount: Number(payload.clickCount ?? 1),
      });
    } else if (action === 'mouseReleased') {
      await page.mouse.up({
        button: String(payload.button ?? 'left'),
        clickCount: Number(payload.clickCount ?? 1),
      });
    }
  }

  async #dispatchWheel(page: PageLike, payload: Record<string, unknown>): Promise<void> {
    if (!page.mouse) {
      throw new Error('active page does not support wheel input');
    }

    const { width, height } = await this.#readViewport(page);
    const x = clampUnitInterval(payload.x) * width;
    const y = clampUnitInterval(payload.y) * height;
    await page.mouse.move(x, y);
    await page.mouse.wheel(Number(payload.deltaX ?? 0), Number(payload.deltaY ?? 0));
  }

  async #dispatchKeyboard(page: PageLike, payload: Record<string, unknown>): Promise<void> {
    if (!page.keyboard) {
      throw new Error('active page does not support keyboard input');
    }

    const action = String(payload.action ?? '');
    const key = String(payload.key ?? payload.code ?? '');
    const text = String(payload.text ?? '');

    if (action === 'keyDown') {
      if (text) {
        await page.keyboard.insertText(text);
      } else {
        await page.keyboard.down(key);
      }
      return;
    }

    if (action === 'keyUp') {
      await page.keyboard.up(key);
      return;
    }

    if (action === 'press') {
      await page.keyboard.press(key);
    }
  }

  async #readViewport(page: PageLike): Promise<{ width: number; height: number }> {
    if (!page.evaluate) {
      return { width: 1280, height: 720 };
    }

    try {
      const viewport = await page.evaluate(() => ({
        width: window.innerWidth || document.documentElement.clientWidth || 1280,
        height: window.innerHeight || document.documentElement.clientHeight || 720,
      }));
      const width = Number(viewport?.width ?? 1280);
      const height = Number(viewport?.height ?? 720);
      return {
        width: Number.isFinite(width) && width > 0 ? width : 1280,
        height: Number.isFinite(height) && height > 0 ? height : 720,
      };
    } catch {
      return { width: 1280, height: 720 };
    }
  }

  async #configureRecorder(session: RuntimeSession, runtime: RuntimeHandles): Promise<void> {
    if (!runtime.context.exposeBinding || !runtime.context.addInitScript) {
      return;
    }

    await runtime.context.exposeBinding('__rpa_emit', async (source, payload) => {
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(payload) as Record<string, unknown>;
      } catch {
        return;
      }

      const sourcePage = source.page;
      const pageAlias = this.#resolvePageAlias(runtime, sourcePage) ?? runtime.activePageAlias ?? 'page';
      if (sourcePage && !runtime.pages.has(pageAlias)) {
        runtime.pages.set(pageAlias, sourcePage);
      }

      const framePath = await this.#buildFramePath(source.frame);
      const action = this.#eventToRecordedAction(session, event, pageAlias, framePath);
      if (!action) {
        return;
      }

      this.#upsertRecordedAction(session, action);
    });

    await runtime.context.addInitScript(RECORDER_INIT_SCRIPT);
  }

  #resolvePageAlias(runtime: RuntimeHandles, page: PageLike | undefined): string | null {
    if (!page) {
      return null;
    }

    for (const [alias, candidate] of runtime.pages.entries()) {
      if (candidate === page) {
        return alias;
      }
    }

    return null;
  }

  async #buildFramePath(frame: unknown): Promise<string[]> {
    if (!frame || typeof frame !== 'object') {
      return [];
    }

    const path: string[] = [];
    let currentFrame: any = frame;
    while (currentFrame) {
      let frameElement: any;
      try {
        frameElement = await currentFrame.frameElement?.();
      } catch {
        break;
      }

      if (!frameElement) {
        break;
      }

      const selector = await this.#describeFrameSelector(frameElement);
      if (!selector) {
        break;
      }

      path.push(selector);
      currentFrame = currentFrame.parentFrame?.();
    }

    path.reverse();
    return path;
  }

  async #describeFrameSelector(frameElement: any): Promise<string | null> {
    try {
      const tagName = String(await frameElement.evaluate?.((element: Element) => element.tagName.toLowerCase()));
      const name = await frameElement.getAttribute?.('name');
      if (name) {
        return `${tagName}[name="${escapeSelectorValue(name)}"]`;
      }

      const title = await frameElement.getAttribute?.('title');
      if (title) {
        return `${tagName}[title="${escapeSelectorValue(title)}"]`;
      }

      const id = await frameElement.getAttribute?.('id');
      if (id) {
        return `${tagName}#${escapeSelectorValue(id)}`;
      }

      return tagName;
    } catch {
      return null;
    }
  }

  #eventToRecordedAction(
    session: RuntimeSession,
    event: Record<string, unknown>,
    pageAlias: string,
    framePath: string[],
  ): RecordedAction | null {
    const kind = mapEventActionToKind(event.action);
    if (!kind) {
      return null;
    }

    const locator = this.#coerceLocatorDescriptor(event.locator);
    const locatorAlternatives = this.#coerceLocatorAlternatives(event.locator_candidates, locator);
    const snapshot = asRecord(event.element_snapshot) ?? {};
    const input = this.#buildActionInput(kind, event);

    return {
      id: `${session.id}-action-${session.actions.length + 1}`,
      sessionId: session.id,
      seq: session.actions.length + 1,
      kind,
      pageAlias,
      framePath,
      locator,
      locatorAlternatives,
      signals: asRecord(event.signals) ?? {},
      input,
      timing: {
        timestamp: Number(event.timestamp ?? Date.now()),
      },
      snapshot,
      status: 'recorded',
    };
  }

  #coerceLocatorDescriptor(value: unknown): { selector: string; locatorAst: Record<string, unknown> } {
    const locator = asRecord(value) ?? {};
    const method = String(locator.method ?? locator.kind ?? 'css');
    const selector = String(locator.selector ?? locator.value ?? '');

    if (method === 'role') {
      return {
        selector,
        locatorAst: {
          kind: 'role',
          role: locator.role ?? 'button',
          name: locator.name ?? '',
        },
      };
    }

    if (method === 'testId' || method === 'testid') {
      return {
        selector,
        locatorAst: {
          kind: 'testId',
          value: locator.value ?? '',
        },
      };
    }

    if (method === 'label' || method === 'placeholder' || method === 'alt' || method === 'title' || method === 'text') {
      return {
        selector,
        locatorAst: {
          kind: method,
          value: locator.value ?? locator.name ?? '',
        },
      };
    }

    return {
      selector,
      locatorAst: {
        kind: 'css',
        value: locator.value ?? selector,
      },
    };
  }

  #coerceLocatorAlternatives(
    value: unknown,
    fallbackLocator: { selector: string; locatorAst: Record<string, unknown> },
  ): Array<{
    selector: string;
    locatorAst: Record<string, unknown>;
    score: number;
    matchCount: number;
    visibleMatchCount: number;
    isSelected: boolean;
    engine: 'playwright';
    reason: string;
  }> {
    if (!Array.isArray(value) || value.length === 0) {
      return [{
        ...fallbackLocator,
        score: 100,
        matchCount: 1,
        visibleMatchCount: 1,
        isSelected: true,
        engine: 'playwright',
        reason: 'selected generated locator',
      }];
    }

    return value.map(candidate => {
      const payload = asRecord(candidate) ?? {};
      const locator = this.#coerceLocatorDescriptor(payload.locator);
      return {
        selector: locator.selector,
        locatorAst: locator.locatorAst,
        score: Number(payload.score ?? 100),
        matchCount: Number(payload.strict_match_count ?? 1),
        visibleMatchCount: Number(payload.visible_match_count ?? 1),
        isSelected: Boolean(payload.selected),
        engine: 'playwright',
        reason: String(payload.reason ?? ''),
      };
    });
  }

  #buildActionInput(kind: RecordedAction['kind'], event: Record<string, unknown>): Record<string, unknown> {
    if (kind === 'fill' || kind === 'selectOption' || kind === 'press') {
      return {
        value: event.value ?? '',
        ...(kind === 'press' ? { key: event.value ?? '' } : {}),
      };
    }

    return {};
  }

  #upsertRecordedAction(session: RuntimeSession, action: RecordedAction): void {
    const lastAction = session.actions.at(-1) as RecordedAction | undefined;
    if (
      lastAction
      && action.kind === 'fill'
      && lastAction.kind === 'fill'
      && lastAction.pageAlias === action.pageAlias
      && lastAction.locator.selector === action.locator.selector
      && JSON.stringify(lastAction.framePath) === JSON.stringify(action.framePath)
    ) {
      lastAction.input = action.input;
      lastAction.snapshot = action.snapshot;
      lastAction.timing = action.timing;
      return;
    }

    session.actions.push(action);
  }
}

function buildLocator(
  scope: PageLike | FrameScopeLike | LocatorLike,
  locatorAst: Record<string, unknown>,
  selector: string,
): LocatorLike {
  const kind = String(locatorAst.kind ?? locatorAst.method ?? inferKind(selector));

  if (kind === 'nested') {
    const parent = asRecord(locatorAst.parent);
    const child = asRecord(locatorAst.child);
    const parentLocator = buildLocator(scope, parent, selector);
    return buildLocator(parentLocator, child, selector);
  }

  if (kind === 'role') {
    const role = String(locatorAst.role ?? inferRole(selector) ?? 'button');
    const name = String(locatorAst.name ?? inferName(selector) ?? '').trim();
    if (name) {
      return scope.getByRole(role, { name, exact: true });
    }
    return scope.getByRole(role);
  }

  if (kind === 'testId' || kind === 'testid') {
    const value = String(locatorAst.value ?? inferTestId(selector) ?? '');
    return scope.getByTestId(value);
  }

  if (kind === 'label') {
    return scope.getByLabel(String(locatorAst.value ?? ''), { exact: true });
  }

  if (kind === 'placeholder') {
    return scope.getByPlaceholder(String(locatorAst.value ?? ''), { exact: true });
  }

  if (kind === 'alt') {
    return scope.getByAltText(String(locatorAst.value ?? ''), { exact: true });
  }

  if (kind === 'title') {
    return scope.getByTitle(String(locatorAst.value ?? ''), { exact: true });
  }

  if (kind === 'text') {
    return scope.getByText(String(locatorAst.value ?? ''), { exact: true });
  }

  const cssValue = String(locatorAst.value ?? selector ?? 'body');
  return scope.locator(cssValue);
}

function inferKind(selector: string): string {
  if (selector.startsWith('internal:role=')) {
    return 'role';
  }
  if (selector.startsWith('internal:testid=')) {
    return 'testId';
  }
  return 'css';
}

function inferRole(selector: string): string | null {
  const match = selector.match(/^internal:role=([a-zA-Z0-9_-]+)/);
  return match?.[1] ?? null;
}

function inferName(selector: string): string | null {
  const match = selector.match(/\[name="([^"]+)"\]/);
  return match?.[1] ?? null;
}

function inferTestId(selector: string): string | null {
  const match = selector.match(/\[data-testid="([^"]+)"\]/);
  return match?.[1] ?? null;
}

function resolveReplayValue(action: RecordedAction, params: Record<string, unknown>): string {
  const rawValue = String(action.input.value ?? action.input.text ?? '');
  for (const [name, value] of Object.entries(params)) {
    if (String(value) === rawValue) {
      return String(params[name]);
    }
  }
  return rawValue;
}

function normalizeUrl(url: string): string {
  if (/^https?:\/\//.test(url)) {
    return url;
  }
  return `https://${url}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function clampUnitInterval(value: unknown): number {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.min(1, Math.max(0, numeric));
}

function mapEventActionToKind(value: unknown): RecordedAction['kind'] | null {
  switch (String(value ?? '')) {
    case 'click':
      return 'click';
    case 'fill':
      return 'fill';
    case 'press':
      return 'press';
    case 'selectOption':
      return 'selectOption';
    case 'check':
      return 'check';
    case 'uncheck':
      return 'uncheck';
    case 'navigate':
      return 'navigate';
    default:
      return null;
  }
}

function escapeSelectorValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

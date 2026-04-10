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
  innerText?(): Promise<string>;
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
  on?(eventName: 'page', listener: (page: PageLike) => void): unknown;
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

type FrameLike = {
  url(): string;
  evaluate?<T>(pageFunction: unknown): Promise<T>;
  childFrames?(): FrameLike[];
  frameElement?(): Promise<unknown>;
  parentFrame?(): FrameLike | null;
};

const POPUP_TRIGGER_ACTION_KINDS = new Set<RecordedAction['kind']>([
  'click',
  'press',
  'selectOption',
  'check',
  'uncheck',
]);

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
      return Array.from(String(value), char => (
        /[A-Za-z0-9_-]/.test(char) ? char : '\\' + char
      )).join('');
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

  const isUniqueSelector = selector => {
    try {
      return document.querySelectorAll(selector).length === 1;
    } catch {
      return false;
    }
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

    if (element.id) {
      const idSelector = '#' + cssEscape(element.id);
      if (isUniqueSelector(idSelector)) {
        return {
          method: 'css',
          value: idSelector,
          selector: idSelector,
        };
      }
    }

    const placeholder = normalize(element.getAttribute('placeholder'));
    if (placeholder) {
      const placeholderSelector = '[placeholder="' + cssEscape(placeholder) + '"]';
      if (isUniqueSelector(placeholderSelector)) {
        return {
          method: 'placeholder',
          value: placeholder,
          selector: placeholderSelector,
        };
      }
    }

    if (element.id) {
      const idSelector = '#' + cssEscape(element.id);
      return {
        method: 'css',
        value: idSelector,
        selector: idSelector,
      };
    }

    if (placeholder) {
      const placeholderSelector = '[placeholder="' + cssEscape(placeholder) + '"]';
      return {
        method: 'placeholder',
        value: placeholder,
        selector: placeholderSelector,
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

const ASSISTANT_SNAPSHOT_SCRIPT = String.raw`(() => {
  const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
  const ROLE_MAP = { A: 'link', BUTTON: 'button', INPUT: 'textbox', TEXTAREA: 'textbox', SELECT: 'combobox' };
  const els = document.querySelectorAll(INTERACTIVE);
  const results = [];
  let index = 1;
  const seen = new Set();
  function cssEsc(s) {
    try { return CSS.escape(s); } catch (e) {
      return String(s || '').replace(/([\\"'\[\](){}|^$.*+?])/g, '\\$1');
    }
  }
  function charType(c) {
    if (c >= 'a' && c <= 'z') return 1;
    if (c >= 'A' && c <= 'Z') return 2;
    if (c >= '0' && c <= '9') return 3;
    return 4;
  }
  function isGuidLike(id) {
    if (!id) return false;
    let transitions = 0;
    for (let i = 1; i < id.length; i++) {
      if (charType(id[i - 1]) !== charType(id[i])) transitions++;
    }
    return transitions >= id.length / 4;
  }
  function getRole(el) {
    return el.getAttribute('role') || ROLE_MAP[el.tagName] || '';
  }
  function stableSelector(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id && !isGuidLike(el.id)) return tag + '#' + cssEsc(el.id);
    const classes = Array.from(el.classList || []).filter(cls => cls && !isGuidLike(cls)).slice(0, 2);
    if (classes.length) return tag + '.' + classes.map(cssEsc).join('.');
    const role = el.getAttribute('role');
    if (role) return tag + '[role="' + cssEsc(role) + '"]';
    return tag;
  }
  function selectorIsUnique(sel) {
    try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
  }
  function buildRelativeSelector(ancestor, el) {
    const parts = [];
    let current = el;
    while (current && current !== ancestor) {
      parts.unshift(stableSelector(current));
      current = current.parentElement;
    }
    if (current !== ancestor) return '';
    return parts.join(' ');
  }
  function countSiblingMatches(el) {
    if (!el || !el.parentElement) return 0;
    const sel = stableSelector(el);
    let count = 0;
    for (const child of Array.from(el.parentElement.children)) {
      try {
        if (child.matches(sel)) count++;
      } catch (e) {}
    }
    return count;
  }
  function findUniqueAnchor(start) {
    let current = start;
    for (let depth = 0; current && current !== document.body && depth < 4; depth++, current = current.parentElement) {
      const sel = stableSelector(current);
      if (selectorIsUnique(sel)) return { node: current, selector: sel };
    }
    return null;
  }
  function findCollectionContext(el) {
    let repeatedRoot = null;
    let current = el.parentElement;
    for (let depth = 0; current && current !== document.body && depth < 6; depth++, current = current.parentElement) {
      if (countSiblingMatches(current) >= 2) {
        repeatedRoot = current;
        break;
      }
    }
    if (!repeatedRoot) return null;
    const itemSelector = buildRelativeSelector(repeatedRoot, el);
    if (!itemSelector) return null;
    const anchor = findUniqueAnchor(repeatedRoot.parentElement || repeatedRoot);
    let containerSelector = stableSelector(repeatedRoot);
    if (anchor && anchor.node !== repeatedRoot) {
      const relativeContainer = buildRelativeSelector(anchor.node, repeatedRoot);
      if (relativeContainer) containerSelector = anchor.selector + ' ' + relativeContainer;
    }
    return {
      container_selector: containerSelector,
      item_selector: itemSelector,
      item_count: countSiblingMatches(repeatedRoot),
    };
  }
  for (const el of els) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (el.disabled) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const tag = el.tagName.toLowerCase();
    const role = getRole(el);
    const name = (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\s+/g, ' ').substring(0, 80);
    const placeholder = el.getAttribute('placeholder') || '';
    const href = el.getAttribute('href') || '';
    const value = el.value || '';
    const type = el.getAttribute('type') || '';
    const key = tag + role + name + placeholder + href;
    if (seen.has(key)) continue;
    seen.add(key);
    const info = { index, tag };
    if (role) info.role = role;
    if (name) info.name = name;
    if (placeholder) info.placeholder = placeholder;
    if (href) info.href = href.substring(0, 120);
    if (value && tag !== 'input') info.value = value.substring(0, 80);
    if (type) info.type = type;
    const collection = findCollectionContext(el);
    if (collection) {
      info.collection_container_selector = collection.container_selector;
      info.collection_item_selector = collection.item_selector;
      info.collection_item_count = collection.item_count;
    }
    results.push(info);
    index++;
    if (results.length >= 80) break;
  }
  return results;
})()`;

export class PlaywrightSessionRuntimeController implements SessionRuntimeController {
  #driver: RuntimeDriver;
  #runtimes = new Map<string, RuntimeHandles>();

  constructor(driver: RuntimeDriver = new PlaywrightDriver()) {
    this.#driver = driver;
  }

  async startSession(session: RuntimeSession): Promise<void> {
    await this.#initializeRuntime(session, { enableRecorder: true });
  }

  async #initializeRuntime(
    session: RuntimeSession,
    options: { enableRecorder: boolean },
  ): Promise<void> {
    await this.stopSession(session.id);
    this.#resetSessionPages(session);
    const browser = await this.#driver.launchBrowser();
    const context = await browser.newContext({ noViewport: true, acceptDownloads: true });
    const runtime: RuntimeHandles = {
      browser,
      context,
      pages: new Map(),
      activePageAlias: 'page',
    };
    if (options.enableRecorder) {
      await this.#configureRecorder(session, runtime);
    }
    const page = await context.newPage();
    runtime.pages.set('page', page);
    this.#runtimes.set(session.id, runtime);
    session.activePageAlias = 'page';
    await this.#syncPageState(session, 'page', page, null);
    this.#bindContextPages(session, runtime);
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
    await this.#initializeRuntime(session, { enableRecorder: false });
    const runtime = this.#requireRuntime(session.id);
    const typedActions = [...(actions as RecordedAction[])];
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

  async captureSnapshot(session: RuntimeSession): Promise<Record<string, unknown>> {
    const runtime = this.#requireRuntime(session.id);
    const alias = runtime.activePageAlias ?? session.activePageAlias ?? 'page';
    const page = await this.#ensurePage(session, runtime, alias);
    const mainFrame = (page as unknown as { mainFrame?: () => FrameLike }).mainFrame?.();

    if (!mainFrame) {
      return {
        url: page.url(),
        title: await page.title(),
        frames: [
          {
            frame_path: [],
            url: page.url(),
            frame_hint: 'main document',
            elements: await this.#extractAssistantElements(page as unknown as FrameLike),
            collections: [],
          },
        ],
      };
    }

    const frames: Array<Record<string, unknown>> = [];
    await this.#collectAssistantFrames(mainFrame, frames);
    return {
      url: page.url(),
      title: await page.title(),
      frames,
    };
  }

  async executeAssistantIntent(
    session: RuntimeSession,
    intent: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const runtime = this.#requireRuntime(session.id);
    const action = String(intent.action ?? '');
    const resolved = asRecord(intent.resolved);
    const actionAlias = runtime.activePageAlias ?? session.activePageAlias ?? 'page';
    const page = await this.#ensurePage(session, runtime, actionAlias);

    if (action === 'navigate') {
      const targetUrl = String(resolved.url ?? intent.value ?? '').trim();
      await page.goto(normalizeUrl(targetUrl));
      await page.waitForLoadState('domcontentloaded');
      await this.#syncPageState(session, actionAlias, page, null);
      return {
        success: true,
        output: 'ok',
        step: {
          action: 'navigate',
          source: 'ai',
          target: '',
          url: targetUrl,
          frame_path: [],
          locator_candidates: [],
          validation: { status: 'ok', details: 'assistant structured action' },
          collection_hint: {},
          item_hint: {},
          ordinal: null,
          assistant_diagnostics: {
            resolved_frame_path: [],
            selected_locator_kind: 'navigate',
            collection_kind: '',
          },
          description: String(intent.description ?? action),
          prompt: intent.prompt ?? null,
          value: intent.value ?? null,
        },
      };
    }

    const framePath = asStringArray(resolved.frame_path);
    const scope = this.#buildAssistantScope(page, framePath);
    const locatorPayload = asRecord(resolved.locator);
    const locator = buildLocatorFromPayload(scope, locatorPayload);
    let output = 'ok';

    if (action === 'click') {
      await locator.click();
    } else if (action === 'extract_text') {
      if (!locator.innerText) {
        throw new Error('locator does not support text extraction');
      }
      output = await locator.innerText();
    } else if (action === 'fill') {
      await locator.fill(String(intent.value ?? ''));
    } else if (action === 'press') {
      await locator.press(String(intent.value ?? 'Enter'));
    } else {
      throw new Error(`Unsupported action: ${action}`);
    }

    await this.#syncPageState(session, actionAlias, page, null);
    const collectionHint = asRecord(resolved.collection_hint);
    const itemHint = asRecord(resolved.item_hint);
    const ordinal = intent.ordinal == null ? null : String(intent.ordinal);

    return {
      success: true,
      output,
      step: {
        action,
        source: 'ai',
        target: JSON.stringify(
          buildCollectionItemPayload(collectionHint, itemHint, ordinal) ?? locatorPayload,
        ),
        frame_path: framePath,
        locator_candidates: Array.isArray(resolved.locator_candidates) ? resolved.locator_candidates : [],
        validation: { status: 'ok', details: 'assistant structured action' },
        collection_hint: collectionHint,
        item_hint: itemHint,
        ordinal,
        assistant_diagnostics: {
          resolved_frame_path: framePath,
          selected_locator_kind: String(resolved.selected_locator_kind ?? ''),
          collection_kind: String(collectionHint.kind ?? ''),
        },
        result_key: intent.result_key ?? null,
        description: String(intent.description ?? action),
        prompt: intent.prompt ?? null,
        value: intent.value ?? null,
      },
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
      await this.#invokeLocatorAction(locator, action, params);
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
      await this.#invokeLocatorAction(locator, action, params);
      const download = await downloadPromise;
      results.download = { filename: download.suggestedFilename() };
      await this.#syncPageState(session, actionAlias, page, null);
      return actionAlias;
    }

    if (navigationSignal) {
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
        this.#invokeLocatorAction(locator, action, params),
      ]);
      await this.#syncPageState(session, actionAlias, page, null);
      return actionAlias;
    }

    await this.#invokeLocatorAction(locator, action, params);

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

  #resetSessionPages(session: RuntimeSession): void {
    session.pages = [];
    session.activePageAlias = null;
  }

  #bindContextPages(session: RuntimeSession, runtime: RuntimeHandles): void {
    runtime.context.on?.('page', page => {
      const existingAlias = this.#resolvePageAlias(runtime, page);
      if (existingAlias) {
        return;
      }

      const openerAlias = runtime.activePageAlias ?? session.activePageAlias ?? 'page';
      const nextAlias = this.#allocatePageAlias(runtime);
      runtime.pages.set(nextAlias, page);
      this.#annotatePopupTrigger(session, openerAlias, nextAlias);
      runtime.activePageAlias = nextAlias;
      session.activePageAlias = nextAlias;

      void (async () => {
        try {
          await page.waitForLoadState('domcontentloaded');
        } catch {
          // Keep best-effort page registration for popups that close or block loading.
        }
        await this.#syncPageState(session, nextAlias, page, openerAlias);
      })();
    });
  }

  #annotatePopupTrigger(session: RuntimeSession, openerAlias: string, targetAlias: string): void {
    for (let index = session.actions.length - 1; index >= 0; index -= 1) {
      const action = session.actions[index] as RecordedAction;
      if (
        !action
        || action.pageAlias !== openerAlias
        || !POPUP_TRIGGER_ACTION_KINDS.has(action.kind)
      ) {
        continue;
      }
      if (action.signals?.popup) {
        return;
      }
      action.signals = {
        ...action.signals,
        popup: { targetPageAlias: targetAlias },
      };
      return;
    }
  }

  async #invokeLocatorAction(
    locator: LocatorLike,
    action: RecordedAction,
    params: Record<string, unknown>,
  ): Promise<void> {
    switch (action.kind) {
      case 'click':
        await locator.click();
        return;
      case 'fill':
        await locator.fill(resolveReplayValue(action, params));
        return;
      case 'press':
        await locator.press(String(action.input.key ?? action.input.value ?? ''));
        return;
      case 'selectOption':
        await locator.selectOption(String(action.input.value ?? ''));
        return;
      case 'check':
        await locator.check();
        return;
      case 'uncheck':
        await locator.uncheck();
        return;
      default:
        throw new Error(`unsupported action kind ${action.kind}`);
    }
  }

  #allocatePageAlias(runtime: RuntimeHandles): string {
    let index = runtime.pages.size + 1;
    let alias = `page-${index}`;
    while (runtime.pages.has(alias)) {
      index += 1;
      alias = `page-${index}`;
    }
    return alias;
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

  #buildAssistantScope(page: PageLike, framePath: string[]): PageLike | FrameScopeLike {
    let scope: PageLike | FrameScopeLike = page;
    for (const selector of framePath) {
      scope = scope.frameLocator(selector);
    }
    return scope;
  }

  async #collectAssistantFrames(frame: FrameLike, target: Array<Record<string, unknown>>): Promise<void> {
    const framePath = await this.#buildFramePath(frame);
    const elements = await this.#extractAssistantElements(frame);
    target.push({
      frame_path: framePath,
      url: frame.url(),
      frame_hint: framePath.length === 0 ? 'main document' : framePath.join(' -> '),
      elements,
      collections: detectAssistantCollections(elements, framePath),
    });

    for (const childFrame of frame.childFrames?.() ?? []) {
      await this.#collectAssistantFrames(childFrame, target);
    }
  }

  async #extractAssistantElements(frame: FrameLike): Promise<Array<Record<string, unknown>>> {
    if (!frame.evaluate) {
      return [];
    }
    try {
      const value = await frame.evaluate(ASSISTANT_SNAPSHOT_SCRIPT);
      return Array.isArray(value) ? value.map(item => asRecord(item)) : [];
    } catch {
      return [];
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

function buildLocatorFromPayload(
  scope: PageLike | FrameScopeLike | LocatorLike,
  payload: Record<string, unknown>,
): LocatorLike {
  const method = String(payload.method ?? 'css');
  if (method === 'role') {
    const role = String(payload.role ?? 'button');
    const name = String(payload.name ?? '').trim();
    return name ? scope.getByRole(role, { name }) : scope.getByRole(role);
  }
  if (method === 'text') {
    return scope.getByText(String(payload.value ?? ''));
  }
  if (method === 'placeholder') {
    return scope.getByPlaceholder(String(payload.value ?? ''));
  }
  if (method === 'title') {
    return scope.getByTitle(String(payload.value ?? ''));
  }
  return scope.locator(String(payload.value ?? ''));
}

function detectAssistantCollections(
  elements: Array<Record<string, unknown>>,
  framePath: string[],
): Array<Record<string, unknown>> {
  const grouped = new Map<string, Array<Record<string, unknown>>>();
  for (const element of elements) {
    const containerSelector = String(element.collection_container_selector ?? '');
    const itemSelector = String(element.collection_item_selector ?? '');
    if (!containerSelector || !itemSelector) {
      continue;
    }
    const key = `${containerSelector}:::${itemSelector}`;
    const current = grouped.get(key) ?? [];
    current.push(element);
    grouped.set(key, current);
  }

  const collections: Array<Record<string, unknown>> = [];
  for (const [key, items] of grouped.entries()) {
    if (items.length < 2) {
      continue;
    }
    const [containerSelector, itemSelector] = key.split(':::');
    const roles = [...new Set(items.map(item => String(item.role ?? '')).filter(Boolean))];
    const itemHint: Record<string, unknown> = {
      locator: { method: 'css', value: itemSelector },
    };
    if (roles.length === 1) {
      itemHint.role = roles[0];
    }
    collections.push({
      kind: 'repeated_items',
      frame_path: [...framePath],
      container_hint: { locator: { method: 'css', value: containerSelector } },
      item_hint: itemHint,
      item_count: items.length,
      items: items.slice(0, 25),
    });
  }

  const links = elements.filter(element => {
    const role = String(element.role ?? '');
    const tag = String(element.tag ?? '');
    return role === 'link' || tag === 'a';
  });
  if (links.length >= 2) {
    collections.push({
      kind: 'search_results',
      frame_path: [...framePath],
      container_hint: { role: 'list' },
      item_hint: { role: 'link' },
      item_count: links.length,
      items: links.slice(0, 10),
    });
  }

  return collections;
}

function buildCollectionItemPayload(
  collectionHint: Record<string, unknown>,
  itemHint: Record<string, unknown>,
  ordinal: string | null,
): Record<string, unknown> | null {
  if (!ordinal) {
    return null;
  }
  const containerLocator = asRecord(asRecord(collectionHint.container_hint).locator);
  const itemLocator = asRecord(itemHint.locator);
  if (Object.keys(containerLocator).length === 0 || Object.keys(itemLocator).length === 0) {
    return null;
  }
  return {
    method: 'collection_item',
    collection: containerLocator,
    ordinal,
    item: itemLocator,
  };
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => String(item)) : [];
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

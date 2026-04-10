import type { RuntimeSession } from '../contracts.js';
import { PlaywrightSessionRuntimeController } from '../playwright/runtime-controller.js';
import { WebSocket } from 'ws';

export interface SessionScreencastBridge {
  handleConnection(socket: WebSocket, session: RuntimeSession): Promise<void>;
}

export class PlaywrightScreencastBridge implements SessionScreencastBridge {
  readonly #runtimeController: PlaywrightSessionRuntimeController;
  readonly #frameIntervalMs: number;

  constructor(
    runtimeController: PlaywrightSessionRuntimeController,
    options: { frameIntervalMs?: number } = {},
  ) {
    this.#runtimeController = runtimeController;
    this.#frameIntervalMs = options.frameIntervalMs ?? 250;
  }

  async handleConnection(socket: WebSocket, session: RuntimeSession): Promise<void> {
    let stopped = false;
    let pushingFrame = false;

    const sendTabsSnapshot = () => {
      if (socket.readyState !== WebSocket.OPEN) {
        return;
      }

      socket.send(JSON.stringify({
        type: 'tabs_snapshot',
        tabs: session.pages.map(page => ({
          tab_id: page.alias,
          title: page.title,
          url: page.url,
          opener_tab_id: page.openerPageAlias,
          status: page.status,
          active: page.alias === session.activePageAlias,
        })),
      }));
    };

    const sendPreviewError = (message: string) => {
      if (socket.readyState !== WebSocket.OPEN) {
        return;
      }

      socket.send(JSON.stringify({
        type: 'preview_error',
        message,
      }));
    };

    const pushFrame = async () => {
      if (stopped || pushingFrame || socket.readyState !== WebSocket.OPEN) {
        return;
      }

      pushingFrame = true;
      try {
        const frame = await this.#runtimeController.captureFrame(session);
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({
            type: 'frame',
            data: frame.data,
            metadata: frame.metadata,
          }));
          sendTabsSnapshot();
        }
      } catch (error) {
        sendPreviewError(error instanceof Error ? error.message : String(error));
      } finally {
        pushingFrame = false;
      }
    };

    const interval = setInterval(() => {
      void pushFrame();
    }, this.#frameIntervalMs);

    socket.on('message', rawPayload => {
      const message = parseSocketMessage(rawPayload);
      if (!message) {
        sendPreviewError('invalid screencast input payload');
        return;
      }

      void this.#runtimeController
        .dispatchInput(session, message)
        .then(() => pushFrame())
        .catch(error => {
          sendPreviewError(error instanceof Error ? error.message : String(error));
        });
    });

    socket.on('close', () => {
      stopped = true;
      clearInterval(interval);
    });

    socket.on('error', () => {
      stopped = true;
      clearInterval(interval);
    });

    sendTabsSnapshot();
    await pushFrame();
  }
}

function parseSocketMessage(rawPayload: unknown): Record<string, unknown> | null {
  try {
    const text = Buffer.isBuffer(rawPayload)
      ? rawPayload.toString('utf-8')
      : Array.isArray(rawPayload)
        ? Buffer.concat(rawPayload).toString('utf-8')
        : String(rawPayload);
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

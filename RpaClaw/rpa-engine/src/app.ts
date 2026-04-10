import Fastify from 'fastify';
import type { FastifyInstance } from 'fastify';
import type { IncomingMessage } from 'node:http';
import type { Socket } from 'node:net';
import { WebSocketServer } from 'ws';
import { loadConfig, type EngineConfig } from './config.js';
import type { SessionRuntimeController } from './contracts.js';
import { EventBus, type EngineEventMap } from './event-bus.js';
import { PlaywrightSessionRuntimeController } from './playwright/runtime-controller.js';
import { registerReplayRoutes } from './routes/replay.js';
import { registerSessionRoutes } from './routes/sessions.js';
import {
  PlaywrightScreencastBridge,
  type SessionScreencastBridge,
} from './screencast/bridge.js';
import { SessionRegistry } from './session-registry.js';

declare module 'fastify' {
  interface FastifyInstance {
    eventBus: EventBus<EngineEventMap>;
    runtimeController: SessionRuntimeController;
    screencastBridge: SessionScreencastBridge;
    sessionRegistry: SessionRegistry;
  }
}

type AppDependencies = {
  runtimeController: SessionRuntimeController;
  screencastBridge: SessionScreencastBridge;
};

export function buildApp(
  overrides: Partial<EngineConfig> = {},
  dependencies: Partial<AppDependencies> = {},
) {
  const config = loadConfig(overrides);
  const app: FastifyInstance = Fastify({ logger: config.NODE_ENV !== 'test' });
  const runtimeController = dependencies.runtimeController ?? new PlaywrightSessionRuntimeController();
  const screencastBridge = dependencies.screencastBridge
    ?? (runtimeController instanceof PlaywrightSessionRuntimeController
      ? new PlaywrightScreencastBridge(runtimeController)
      : new UnsupportedScreencastBridge());
  const screencastServer = new WebSocketServer({ noServer: true });

  app.decorate('eventBus', new EventBus<EngineEventMap>());
  app.decorate('runtimeController', runtimeController);
  app.decorate('screencastBridge', screencastBridge);
  app.decorate('sessionRegistry', new SessionRegistry());

  app.get('/health', async () => ({
    status: 'ok',
    service: 'rpa-engine',
  }));

  app.register(registerSessionRoutes);
  app.register(registerReplayRoutes);
  app.server.on('upgrade', (request, socket, head) => {
    handleScreencastUpgrade(app, screencastServer, request, socket, head);
  });
  app.addHook('onClose', async () => {
    await new Promise<void>((resolve, reject) => {
      screencastServer.close(error => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      });
    });
  });

  return app;
}

function handleScreencastUpgrade(
  app: FastifyInstance,
  screencastServer: WebSocketServer,
  request: IncomingMessage,
  socket: Socket,
  head: Buffer,
): void {
  const rawUrl = request.url ?? '/';
  const url = new URL(rawUrl, 'http://127.0.0.1');
  const match = url.pathname.match(/^\/sessions\/([^/]+)\/screencast$/);

  if (!match) {
    socket.write('HTTP/1.1 404 Not Found\r\n\r\n');
    socket.destroy();
    return;
  }

  const session = app.sessionRegistry.get(match[1]);
  if (!session) {
    socket.write('HTTP/1.1 404 Not Found\r\n\r\n');
    socket.destroy();
    return;
  }

  screencastServer.handleUpgrade(
    request,
    socket,
    head,
    websocket => {
      void app.screencastBridge.handleConnection(websocket, session).catch(error => {
        app.log.error(error, 'screencast bridge failed');
        websocket.close();
      });
    },
  );
}

class UnsupportedScreencastBridge implements SessionScreencastBridge {
  async handleConnection(): Promise<void> {
    throw new Error('screencast bridge requires a live Playwright runtime controller');
  }
}

import Fastify from 'fastify';
import type { FastifyInstance } from 'fastify';
import { loadConfig, type EngineConfig } from './config.js';
import type { SessionRuntimeController } from './contracts.js';
import { EventBus, type EngineEventMap } from './event-bus.js';
import { PlaywrightSessionRuntimeController } from './playwright/runtime-controller.js';
import { registerReplayRoutes } from './routes/replay.js';
import { registerSessionRoutes } from './routes/sessions.js';
import { SessionRegistry } from './session-registry.js';

declare module 'fastify' {
  interface FastifyInstance {
    eventBus: EventBus<EngineEventMap>;
    runtimeController: SessionRuntimeController;
    sessionRegistry: SessionRegistry;
  }
}

type AppDependencies = {
  runtimeController: SessionRuntimeController;
};

export function buildApp(
  overrides: Partial<EngineConfig> = {},
  dependencies: Partial<AppDependencies> = {},
) {
  const config = loadConfig(overrides);
  const app: FastifyInstance = Fastify({ logger: config.NODE_ENV !== 'test' });

  app.decorate('eventBus', new EventBus<EngineEventMap>());
  app.decorate('runtimeController', dependencies.runtimeController ?? new PlaywrightSessionRuntimeController());
  app.decorate('sessionRegistry', new SessionRegistry());

  app.get('/health', async () => ({
    status: 'ok',
    service: 'rpa-engine',
  }));

  app.register(registerSessionRoutes);
  app.register(registerReplayRoutes);

  return app;
}

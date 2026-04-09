import Fastify from 'fastify';
import type { FastifyInstance } from 'fastify';
import { loadConfig, type EngineConfig } from './config.js';
import { EventBus, type EngineEventMap } from './event-bus.js';
import { registerSessionRoutes } from './routes/sessions.js';
import { SessionRegistry } from './session-registry.js';

declare module 'fastify' {
  interface FastifyInstance {
    eventBus: EventBus<EngineEventMap>;
    sessionRegistry: SessionRegistry;
  }
}

export function buildApp(overrides: Partial<EngineConfig> = {}) {
  const config = loadConfig(overrides);
  const app: FastifyInstance = Fastify({ logger: config.NODE_ENV !== 'test' });

  app.decorate('eventBus', new EventBus<EngineEventMap>());
  app.decorate('sessionRegistry', new SessionRegistry());

  app.get('/health', async () => ({
    status: 'ok',
    service: 'rpa-engine',
  }));

  app.register(registerSessionRoutes);

  return app;
}

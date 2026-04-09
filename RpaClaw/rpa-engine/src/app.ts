import Fastify from 'fastify';
import { loadConfig, type EngineConfig } from './config.js';

export function buildApp(overrides: Partial<EngineConfig> = {}) {
  const config = loadConfig(overrides);
  const app = Fastify({ logger: config.NODE_ENV !== 'test' });

  app.get('/health', async () => ({
    status: 'ok',
    service: 'rpa-engine',
  }));

  return app;
}

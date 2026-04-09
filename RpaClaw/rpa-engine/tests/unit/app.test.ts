import { describe, expect, it } from 'vitest';
import { buildApp } from '../../src/app.js';

describe('engine health endpoint', () => {
  it('returns the service name and mode', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });
    const response = await app.inject({ method: 'GET', url: '/health' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: 'ok',
      service: 'rpa-engine',
    });
  });
});

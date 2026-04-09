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

  it('creates and retrieves runtime sessions', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1' },
    });

    expect(createResponse.statusCode).toBe(200);
    const created = createResponse.json().session as {
      id: string;
      userId: string;
      mode: string;
    };
    expect(created.userId).toBe('u1');
    expect(created.mode).toBe('idle');

    const getResponse = await app.inject({
      method: 'GET',
      url: `/sessions/${created.id}`,
    });

    expect(getResponse.statusCode).toBe(200);
    expect(getResponse.json()).toEqual({
      session: created,
    });
  });
});

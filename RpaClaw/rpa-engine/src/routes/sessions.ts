import type { FastifyInstance } from 'fastify';
import { createRuntimeSession } from '../playwright/runtime-session.js';

export async function registerSessionRoutes(app: FastifyInstance) {
  app.post('/sessions', async request => {
    const body = request.body as { userId: string };
    const session = createRuntimeSession({ userId: body.userId });

    app.sessionRegistry.set(session);
    app.eventBus.publish('session.created', session);

    return { session };
  });

  app.get('/sessions/:id', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);

    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    return { session };
  });
}

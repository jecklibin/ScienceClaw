import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { createRuntimeSession, ensureRuntimePage } from '../playwright/runtime-session.js';

const createSessionBodySchema = z.object({
  userId: z.string().trim().min(1),
  sandboxSessionId: z.string().trim().optional(),
});

const activateSessionBodySchema = z.object({
  pageAlias: z.string().trim().min(1),
});

const navigateSessionBodySchema = z.object({
  url: z.string().trim().min(1),
  pageAlias: z.string().trim().optional(),
});

export async function registerSessionRoutes(app: FastifyInstance) {
  app.post('/sessions', async (request, reply) => {
    const parsedBody = createSessionBodySchema.safeParse(request.body);
    if (!parsedBody.success) {
      return reply.code(400).send({
        message: 'body must be an object with a non-empty userId',
      });
    }

    const session = createRuntimeSession({
      userId: parsedBody.data.userId,
      sandboxSessionId: parsedBody.data.sandboxSessionId,
    });

    await app.runtimeController.startSession(session);
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

  app.post('/sessions/:id/activate', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const parsedBody = activateSessionBodySchema.safeParse(request.body);
    if (!parsedBody.success) {
      return reply.code(400).send({
        message: 'body must be an object with a non-empty pageAlias',
      });
    }

    ensureRuntimePage(session, parsedBody.data.pageAlias);
    await app.runtimeController.activatePage(session, parsedBody.data.pageAlias);
    app.eventBus.publish('session.updated', session);
    return { session };
  });

  app.post('/sessions/:id/navigate', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const parsedBody = navigateSessionBodySchema.safeParse(request.body);
    if (!parsedBody.success) {
      return reply.code(400).send({
        message: 'body must be an object with a non-empty url',
      });
    }

    const normalizedUrl = normalizeUrl(parsedBody.data.url);
    const pageAlias = parsedBody.data.pageAlias ?? session.activePageAlias ?? 'page';
    const page = ensureRuntimePage(session, pageAlias);
    page.url = normalizedUrl;
    await app.runtimeController.navigate(session, normalizedUrl, pageAlias);
    app.eventBus.publish('session.updated', session);
    return { session };
  });

  app.post('/sessions/:id/stop', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    await app.runtimeController.stopSession(id);
    session.mode = 'stopped';
    session.status = 'stopped';
    app.eventBus.publish('session.updated', session);
    return { session };
  });
}

function normalizeUrl(url: string): string {
  if (/^https?:\/\//.test(url)) {
    return url;
  }
  return `https://${url}`;
}

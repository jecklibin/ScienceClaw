import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import type { RecordedAction } from '../action-model.js';
import { generatePythonCode } from '../replay/codegen.js';
import { runReplay } from '../replay/replay-runner.js';

const recordedActionSchema = z
  .object({
    id: z.string(),
    sessionId: z.string(),
    seq: z.number(),
    kind: z.string(),
    pageAlias: z.string(),
    framePath: z.array(z.string()),
    locator: z.object({
      selector: z.string(),
      locatorAst: z.record(z.unknown()),
    }),
    locatorAlternatives: z.array(z.unknown()),
    signals: z.record(z.unknown()),
    input: z.record(z.unknown()),
    timing: z.record(z.unknown()),
    snapshot: z.record(z.unknown()),
    status: z.string(),
  })
  .passthrough();

const replayBodySchema = z
  .object({
    actions: z.array(recordedActionSchema).optional(),
    params: z.record(z.unknown()).optional(),
  })
  .default({});

function resolveActions(
  body: z.infer<typeof replayBodySchema>,
  sessionActions: unknown[],
): RecordedAction[] {
  if (body.actions && body.actions.length > 0) {
    return body.actions as RecordedAction[];
  }

  return sessionActions as RecordedAction[];
}

export async function registerReplayRoutes(app: FastifyInstance) {
  app.post('/sessions/:id/codegen', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const parsedBody = replayBodySchema.safeParse(request.body ?? {});
    if (!parsedBody.success) {
      return reply.code(400).send({ message: 'body must be a replay request object' });
    }

    const actions = resolveActions(parsedBody.data, session.actions);
    return {
      script: generatePythonCode(actions, parsedBody.data.params),
    };
  });

  app.post('/sessions/:id/replay', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const parsedBody = replayBodySchema.safeParse(request.body ?? {});
    if (!parsedBody.success) {
      return reply.code(400).send({ message: 'body must be a replay request object' });
    }

    const actions = resolveActions(parsedBody.data, session.actions);
    return runReplay(
      actions,
      parsedBody.data.params,
      ({ actions: replayActions, params }) =>
        app.runtimeController.replay(session, replayActions, params),
    );
  });
}

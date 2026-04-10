# RPA Engine

`RpaClaw/rpa-engine` is the Playwright-native recorder and replay runtime used when the backend runs with `RPA_ENGINE_MODE=node`.

## Local Development

Run the engine as a local process on the same machine as the backend:

```bash
cd RpaClaw/rpa-engine
npm install
npm run dev
```

Backend settings:

```bash
RPA_ENGINE_MODE=node
RPA_ENGINE_BASE_URL=http://127.0.0.1:3310
```

The default local listener is `127.0.0.1:3310`.

## Cloud Or Split Deployment

Deploy `rpa-engine` separately when the backend and browser runtime are split across hosts or environments.

Backend settings:

```bash
RPA_ENGINE_MODE=node
RPA_ENGINE_BASE_URL=https://your-engine.example.internal
RPA_ENGINE_AUTH_TOKEN=
```

Use `RPA_ENGINE_AUTH_TOKEN` if the service sits behind an internal auth gateway or reverse proxy.

## Commands

```bash
npm run dev
npm run build
npm test
```

## Main Files

- `src/app.ts` - Fastify app wiring
- `src/routes/sessions.ts` - session lifecycle APIs
- `src/routes/replay.ts` - replay and code generation APIs
- `src/playwright/runtime-controller.ts` - browser/page/frame runtime ownership
- `src/playwright/recorder-adapter.ts` - recorder action adaptation

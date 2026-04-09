import { buildApp } from './app.js';
import { loadConfig } from './config.js';

const config = loadConfig();
const app = buildApp(config);

app.listen({ host: config.RPA_ENGINE_HOST, port: config.RPA_ENGINE_PORT }).catch((err: unknown) => {
  app.log.error(err);
  process.exit(1);
});

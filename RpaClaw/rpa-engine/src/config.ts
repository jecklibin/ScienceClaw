export type EngineConfig = {
  NODE_ENV: string;
  RPA_ENGINE_HOST: string;
  RPA_ENGINE_PORT: number;
  RPA_ENGINE_AUTH_TOKEN: string;
};

export function loadConfig(overrides: Partial<EngineConfig> = {}): EngineConfig {
  return {
    NODE_ENV: overrides.NODE_ENV ?? process.env.NODE_ENV ?? 'development',
    RPA_ENGINE_HOST: overrides.RPA_ENGINE_HOST ?? process.env.RPA_ENGINE_HOST ?? '127.0.0.1',
    RPA_ENGINE_PORT:
      overrides.RPA_ENGINE_PORT ?? Number(process.env.RPA_ENGINE_PORT ?? '3310'),
    RPA_ENGINE_AUTH_TOKEN:
      overrides.RPA_ENGINE_AUTH_TOKEN ?? process.env.RPA_ENGINE_AUTH_TOKEN ?? '',
  };
}

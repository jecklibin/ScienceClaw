import * as fs from 'fs';
import * as path from 'path';
import { resolveHomeLayout } from './home-layout';

const CONFIG_FILE = 'app-config.json';
const ENV_FILE = '.env';

export interface RuntimePaths {
  installRootDir: string;
  resourceDir: string;
  configFilePath: string;
  envFilePath: string;
}

interface ResolveRuntimePathsOptions {
  isPackaged: boolean;
  execPath: string;
  resourcesPath: string;
  currentDir: string;
}

interface BuildBackendEnvOptions {
  homeDir: string;
  resourceDir: string;
  extraEnv?: Record<string, string>;
}

function findEnvKey(
  env: Record<string, string> | NodeJS.ProcessEnv | undefined,
  targetKey: string
): string | undefined {
  if (!env) {
    return undefined;
  }

  const loweredTarget = targetKey.toLowerCase();
  return Object.keys(env).find((key) => key.toLowerCase() === loweredTarget);
}

function prependPathEntry(pathEntry: string, existingPath?: string): string {
  const normalizedTarget = path.normalize(pathEntry);
  const isWindows = process.platform === 'win32';
  const matches = (candidate: string): boolean => {
    const normalizedCandidate = path.normalize(candidate);
    return isWindows
      ? normalizedCandidate.toLowerCase() === normalizedTarget.toLowerCase()
      : normalizedCandidate === normalizedTarget;
  };

  const segments = (existingPath ?? '')
    .split(path.delimiter)
    .map((segment) => segment.trim())
    .filter(Boolean)
    .filter((segment) => !matches(segment));

  return [pathEntry, ...segments].join(path.delimiter);
}

export function resolveRuntimePaths(options: ResolveRuntimePathsOptions): RuntimePaths {
  const devRootDir = path.resolve(options.currentDir, '..', '..');
  const resourceDir = options.isPackaged ? options.resourcesPath : devRootDir;
  const installRootDir = options.isPackaged ? path.dirname(options.execPath) : devRootDir;

  return {
    installRootDir,
    resourceDir,
    configFilePath: path.join(installRootDir, CONFIG_FILE),
    envFilePath: path.join(installRootDir, ENV_FILE),
  };
}

export function resolveHomeEnvFilePath(homeDir: string): string {
  return path.join(homeDir, ENV_FILE);
}

export function parseEnvContent(content: string): Record<string, string> {
  const entries: Record<string, string> = {};

  for (const rawLine of content.split(/\r?\n/u)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separatorIndex = line.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    if (!key) {
      continue;
    }

    let value = line.slice(separatorIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    entries[key] = value;
  }

  return entries;
}

export function loadEnvFile(filePath: string): Record<string, string> {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  return parseEnvContent(fs.readFileSync(filePath, 'utf-8'));
}

export function buildBackendEnv(options: BuildBackendEnvOptions): Record<string, string> {
  const homeLayout = resolveHomeLayout(options.homeDir);
  const pythonDir = path.join(options.resourceDir, 'python');
  const sitePackages = path.join(pythonDir, 'Lib', 'site-packages');
  const nodeDir = path.join(options.resourceDir, 'node');
  const nodeModules = path.join(nodeDir, 'node_modules');
  const playwrightBrowsers = path.join(
    sitePackages,
    'playwright',
    'driver',
    'package',
    '.local-browsers'
  );
  const frontendDist = path.join(options.resourceDir, 'frontend-dist');
  const pathKey = findEnvKey(options.extraEnv, 'PATH') ?? findEnvKey(process.env, 'PATH') ?? 'PATH';
  const inheritedPath = options.extraEnv?.[pathKey] ?? process.env[pathKey] ?? '';
  const runtimePath = prependPathEntry(pythonDir, prependPathEntry(nodeDir, inheritedPath));

  return {
    STORAGE_BACKEND: 'local',
    AUTH_PROVIDER: 'none',
    RPA_CLAW_HOME: options.homeDir,
    WORKSPACE_DIR: homeLayout.workspaceDir,
    TOOLS_DIR: homeLayout.toolsDir,
    EXTERNAL_SKILLS_DIR: homeLayout.externalSkillsDir,
    LOCAL_DATA_DIR: homeLayout.dataDir,
    BUILTIN_SKILLS_DIR: path.join(options.resourceDir, 'builtin_skills'),
    BACKEND_PORT: '12001',
    TASK_SERVICE_PORT: '12002',
    PYTHONHOME: pythonDir,
    PYTHONPATH: sitePackages,
    NODE_PATH: nodeModules,
    PLAYWRIGHT_BROWSERS_PATH: playwrightBrowsers,
    ENVIRONMENT: 'production',
    LOG_LEVEL: 'INFO',
    FRONTEND_DIST_DIR: frontendDist,
    ...options.extraEnv,
    [pathKey]: runtimePath,
  };
}

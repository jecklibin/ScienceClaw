import { apiClient, ApiResponse } from './client';

export interface ModelConfig {
  id: string;
  name: string;
  provider: string;
  base_url?: string;
  api_key?: string;
  model_name: string;
  context_window?: number | null;
  is_system: boolean;
  user_id?: string;
  is_active: boolean;
  auth_credential_id?: string | null;
  auth_credential_owned?: boolean;
  auth_config?: ModelAuthConfig | null;
  created_at: number;
  updated_at: number;
}

export interface ModelAuthCredentialRef {
  alias: string;
  credential_id: string;
  owned_by_model?: boolean;
}

export interface StaticHeadersAuthConfig {
  version: number;
  type: 'static_headers';
  credentials: ModelAuthCredentialRef[];
  headers: Record<string, string>;
  query?: Record<string, string>;
}

export interface TokenRequestConfig {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH';
  url: string;
  headers?: Record<string, string>;
  query?: Record<string, string>;
  body_type?: 'json' | 'form' | 'raw';
  body?: unknown;
}

export interface TokenInjectConfig {
  headers?: Record<string, string>;
  query?: Record<string, string>;
  body?: Record<string, unknown>;
}

export interface DynamicTokenAuthConfig {
  version: number;
  type: 'dynamic_token';
  credentials: ModelAuthCredentialRef[];
  token_request: TokenRequestConfig;
  inject: TokenInjectConfig;
}

export type ModelAuthConfig = StaticHeadersAuthConfig | DynamicTokenAuthConfig;

export interface StaticHeaderSaveInput {
  name: string;
  value?: string;
  credential_id?: string;
}

export interface DynamicTokenCredentialSaveInput {
  alias: string;
  username?: string;
  password?: string;
  domain?: string;
  credential_id?: string;
  name?: string;
}

export interface DynamicTokenSaveInput {
  credentials: DynamicTokenCredentialSaveInput[];
  token_request: TokenRequestConfig;
  inject: TokenInjectConfig;
}

export interface DynamicTokenTestField {
  path: string;
  value: unknown;
  type: string;
}

export interface DynamicTokenTestResult {
  status_code: number;
  ok: boolean;
  body: unknown;
  fields: DynamicTokenTestField[];
}

export type ModelAuthSaveRequest =
  | { type: 'none'; static_headers?: [] }
  | { type: 'static_headers'; static_headers: StaticHeaderSaveInput[] }
  | { type: 'dynamic_token'; dynamic_token: DynamicTokenSaveInput };

export interface CreateModelRequest {
  name: string;
  provider: string;
  base_url?: string;
  api_key?: string;
  model_name: string;
  context_window?: number | null;
  auth_config?: ModelAuthSaveRequest | null;
  auth_credential_id?: string | null;
}

export interface UpdateModelRequest {
  name?: string;
  base_url?: string;
  api_key?: string;
  model_name?: string;
  context_window?: number | null;
  is_active?: boolean;
  auth_config?: ModelAuthSaveRequest | null;
  auth_credential_id?: string | null;
}

export async function listModels(): Promise<ModelConfig[]> {
  const response = await apiClient.get<ApiResponse<ModelConfig[]>>('/models');
  return response.data.data;
}

export async function createModel(data: CreateModelRequest): Promise<ModelConfig> {
  const response = await apiClient.post<ApiResponse<ModelConfig>>('/models', data);
  return response.data.data;
}

export async function updateModel(id: string, data: UpdateModelRequest): Promise<void> {
  await apiClient.put(`/models/${id}`, data);
}

export async function deleteModel(id: string): Promise<void> {
  await apiClient.delete(`/models/${id}`);
}

export async function detectContextWindow(data: { provider: string; base_url?: string; api_key?: string; model_name: string; model_id?: string }): Promise<number> {
  const response = await apiClient.post<ApiResponse<{ context_window: number }>>('/models/detect-context-window', data);
  return response.data.data.context_window;
}

export async function testDynamicToken(data: DynamicTokenSaveInput): Promise<DynamicTokenTestResult> {
  const response = await apiClient.post<ApiResponse<DynamicTokenTestResult>>('/models/test-dynamic-token', {
    dynamic_token: data,
  });
  return response.data.data;
}

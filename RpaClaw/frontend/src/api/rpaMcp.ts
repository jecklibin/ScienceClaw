import { apiClient, ApiResponse } from './client';

export interface JsonSchemaObject {
  type?: string | string[];
  properties?: Record<string, JsonSchemaObject>;
  items?: JsonSchemaObject;
  required?: string[];
  description?: string;
  default?: unknown;
  additionalProperties?: boolean;
  [key: string]: unknown;
}

export type RpaMcpSchemaSource = 'ai_inferred' | 'rule_inferred' | 'user_edited' | string;

export interface RpaMcpSemanticInference {
  source: RpaMcpSchemaSource;
  confidence?: number | null;
  warnings?: string[];
  model?: string;
  generated_at?: string;
}

export interface RpaMcpExecutionResult {
  success: boolean;
  message?: string;
  data: Record<string, unknown>;
  downloads: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  error?: Record<string, unknown> | null;
}

export interface RpaMcpExecutionPlan {
  tool_id: string;
  generated_at: string;
  requires_cookies: boolean;
  compiled_steps: Array<Record<string, unknown>>;
  compiled_script: string;
  input_schema: JsonSchemaObject;
  output_schema: JsonSchemaObject;
  source_hash: string;
}

export interface RpaMcpPreview {
  id?: string;
  name: string;
  tool_name: string;
  description: string;
  enabled?: boolean;
  requires_cookies?: boolean;
  allowed_domains: string[];
  post_auth_start_url: string;
  steps: Record<string, unknown>[];
  params: Record<string, unknown>;
  input_schema: JsonSchemaObject;
  output_schema: JsonSchemaObject;
  recommended_output_schema: JsonSchemaObject;
  output_schema_confirmed?: boolean;
  output_examples?: Array<Record<string, unknown>>;
  output_inference_report?: Record<string, unknown>;
  schema_source?: RpaMcpSchemaSource;
  semantic_inference?: RpaMcpSemanticInference;
  sanitize_report: {
    removed_steps: number[];
    removed_step_details?: Array<{
      index: number;
      action?: string;
      description?: string;
      url?: string;
    }>;
    removed_params: string[];
    warnings: string[];
  };
  source?: Record<string, unknown>;
}

export interface RpaMcpToolItem extends RpaMcpPreview {
  id: string;
  enabled: boolean;
}

export async function previewRpaMcpTool(sessionId: string, payload: { name: string; description?: string; allowed_domains?: string[]; post_auth_start_url?: string; input_schema?: JsonSchemaObject; params?: Record<string, unknown>; schema_source?: RpaMcpSchemaSource }) {
  const response = await apiClient.post<ApiResponse<RpaMcpPreview>>(`/rpa-mcp/session/${encodeURIComponent(sessionId)}/preview`, payload);
  return response.data.data;
}

export async function testPreviewRpaMcpTool(
  sessionId: string,
  payload: {
    name: string;
    description?: string;
    allowed_domains?: string[];
    post_auth_start_url?: string;
    input_schema?: JsonSchemaObject;
    params?: Record<string, unknown>;
    schema_source?: RpaMcpSchemaSource;
    cookies?: Array<Record<string, unknown>>;
    arguments?: Record<string, unknown>;
  },
) {
  const response = await apiClient.post<ApiResponse<RpaMcpExecutionResult>>(`/rpa-mcp/session/${encodeURIComponent(sessionId)}/test-preview`, payload);
  return response.data.data;
}

export async function createRpaMcpTool(sessionId: string, payload: Record<string, unknown>) {
  const response = await apiClient.post<ApiResponse<RpaMcpToolItem>>(`/rpa-mcp/session/${encodeURIComponent(sessionId)}/tools`, payload);
  return response.data.data;
}

export async function listRpaMcpTools() {
  const response = await apiClient.get<ApiResponse<RpaMcpToolItem[]>>('/rpa-mcp/tools');
  return response.data.data;
}

export async function getRpaMcpTool(toolId: string) {
  const response = await apiClient.get<ApiResponse<RpaMcpToolItem>>(`/rpa-mcp/tools/${encodeURIComponent(toolId)}`);
  return response.data.data;
}

export async function getRpaMcpExecutionPlan(toolId: string) {
  const response = await apiClient.get<ApiResponse<RpaMcpExecutionPlan>>(`/rpa-mcp/tools/${encodeURIComponent(toolId)}/execution-plan`);
  return response.data.data;
}

export async function updateRpaMcpTool(toolId: string, payload: Record<string, unknown>) {
  const response = await apiClient.put<ApiResponse<RpaMcpToolItem>>(`/rpa-mcp/tools/${encodeURIComponent(toolId)}`, payload);
  return response.data.data;
}

export async function deleteRpaMcpTool(toolId: string) {
  const response = await apiClient.delete<ApiResponse<{ id: string; deleted: boolean }>>(`/rpa-mcp/tools/${encodeURIComponent(toolId)}`);
  return response.data.data;
}

export async function testRpaMcpTool(toolId: string, payload: { cookies?: Array<Record<string, unknown>>; arguments?: Record<string, unknown> }) {
  const response = await apiClient.post<ApiResponse<RpaMcpExecutionResult>>(`/rpa-mcp/tools/${encodeURIComponent(toolId)}/test`, payload);
  return response.data.data;
}

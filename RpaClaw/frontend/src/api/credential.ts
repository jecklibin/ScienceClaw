import { apiClient } from './client';

export interface Credential {
  id: string;
  kind?: 'basic' | 'model_auth';
  name: string;
  description?: string;
  username: string;
  domain: string;
  model_auth?: ModelAuthCredentialPayload | null;
  created_at: string;
  updated_at: string;
}

export interface ModelAuthVariable {
  sensitive: boolean;
  value?: string;
  has_value?: boolean;
}

export interface ModelAuthCredentialPayload {
  type: 'static_headers' | 'dynamic_token';
  config: Record<string, any>;
  variables: Record<string, ModelAuthVariable>;
}

export interface CredentialCreate {
  kind?: 'basic' | 'model_auth';
  name: string;
  description?: string;
  username: string;
  password: string;
  domain?: string;
  model_auth?: ModelAuthCredentialPayload | null;
}

export interface CredentialUpdate {
  name?: string;
  description?: string;
  username?: string;
  password?: string;
  domain?: string;
  model_auth?: ModelAuthCredentialPayload | null;
}

export async function listCredentials(): Promise<Credential[]> {
  const resp = await apiClient.get('/credentials');
  return resp.data.credentials;
}

export async function createCredential(data: CredentialCreate): Promise<Credential> {
  const resp = await apiClient.post('/credentials', data);
  return resp.data.credential;
}

export async function updateCredential(id: string, data: CredentialUpdate): Promise<Credential> {
  const resp = await apiClient.put(`/credentials/${id}`, data);
  return resp.data.credential;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`/credentials/${id}`);
}

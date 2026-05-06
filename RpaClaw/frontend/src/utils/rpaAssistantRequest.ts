export function buildRpaAssistantRequestHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  const token = localStorage.getItem('access_token');
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

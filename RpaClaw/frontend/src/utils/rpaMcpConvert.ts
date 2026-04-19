import type { RpaMcpExecutionResult } from '@/api/rpaMcp';

export type PreviewTestStatus = 'untested' | 'failed' | 'stale' | 'success';

export interface PreviewDraftSignatureInput {
  sessionId: string;
  name: string;
  description: string;
  allowedDomains: string[];
  postAuthStartUrl: string;
}

export interface PreviewTestStatusInput {
  hasMatchingSuccessfulTest: boolean;
  testResult: RpaMcpExecutionResult | null;
  hasConfigChangesSinceLastTest: boolean;
}

function normalizeList(items: string[]): string[] {
  return items.map((item) => item.trim()).filter(Boolean);
}

export function buildPreviewDraftSignature(input: PreviewDraftSignatureInput): string {
  return JSON.stringify({
    session_id: input.sessionId.trim(),
    name: input.name.trim(),
    description: input.description.trim(),
    allowed_domains: normalizeList(input.allowedDomains),
    post_auth_start_url: input.postAuthStartUrl.trim(),
  });
}

export function hasMatchingPreviewTest(currentSignature: string, lastSuccessfulSignature: string | null): boolean {
  return Boolean(lastSuccessfulSignature) && currentSignature === lastSuccessfulSignature;
}

export function getPreviewTestStatus(input: PreviewTestStatusInput): PreviewTestStatus {
  if (input.hasMatchingSuccessfulTest) return 'success';
  if (input.hasConfigChangesSinceLastTest && input.testResult?.success) return 'stale';
  if (input.testResult) return input.testResult.success ? 'success' : 'failed';
  return 'untested';
}

export function focusPreviewTestSection(section: HTMLElement | null): void {
  if (!section) return;
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const action = section.querySelector<HTMLElement>('[data-preview-test-action]');
  action?.focus({ preventScroll: true });
}

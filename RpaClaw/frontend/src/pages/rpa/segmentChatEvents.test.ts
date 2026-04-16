import { describe, expect, it } from 'vitest';
import {
  applySegmentEvent,
  createAssistantMessage,
  toPolledRecordedAiStep,
  toRecordedAiStep,
} from './segmentChatEvents';

describe('applySegmentEvent', () => {
  it('planned and recovery events append diagnostics without marking failure', () => {
    const message = createAssistantMessage('10:30');
    message.status = 'executing';

    const planned = applySegmentEvent(message, 'segment_planned', {
      thought: 'compare visible rows first',
      segment_goal: '动态比较 stars 并点击最高项',
      segment_kind: 'state_changing',
      stop_reason: 'after_state_change',
    });
    const recovering = applySegmentEvent(planned, 'segment_recovering', {
      segment_goal: '动态比较 stars 并点击最高项',
      error_code: 'invalid_generated_code',
      message: 'Generated code appears to be JavaScript.',
      attempt: 1,
    });

    expect(recovering.status).toBe('executing');
    expect(recovering.error).toBeUndefined();
    expect(recovering.diagnostics).toContain('Segment: 动态比较 stars 并点击最高项');
    expect(recovering.diagnostics).toContain('Recovery 1: 动态比较 stars 并点击最高项');
    expect(recovering.diagnostics).toContain('Error code: invalid_generated_code');
  });

  it('recording_aborted sets error status and error message', () => {
    const message = createAssistantMessage('10:30');
    message.status = 'executing';

    const next = applySegmentEvent(message, 'recording_aborted', {
      segment_goal: '动态比较 stars 并点击最高项',
      reason: 'Timed out waiting for page change',
    });

    expect(next.status).toBe('error');
    expect(next.error).toBe('Timed out waiting for page change');
    expect(next.diagnostics).toContain('Aborted: 动态比较 stars 并点击最高项');
  });
});

describe('toRecordedAiStep', () => {
  it('maps one committed segment into one sidebar step', () => {
    const recorded = toRecordedAiStep(
      {
        action: 'ai_script',
        source: 'ai',
        description: '动态比较 stars 并点击最高项',
        prompt: '点击 stars 最多的项目',
        sensitive: false,
        frame_path: ['iframe[name="workspace"]'],
        assistant_diagnostics: {
          execution_mode: 'segment',
          segment_kind: 'state_changing',
          stop_reason: 'after_state_change',
          recovery_attempts: 1,
        },
      },
      3,
    );

    expect(recorded).toEqual({
      id: '3',
      title: '动态比较 stars 并点击最高项',
      description: '点击 stars 最多的项目',
      status: 'completed',
      source: 'ai',
      sensitive: false,
      frameSummary: 'iframe[name="workspace"]',
      diagnostics: ['Segment kind: state_changing', 'Stop reason: after_state_change', 'Recovery attempts: 1'],
    });
  });

  it('preserves diagnostics for polled segment steps', () => {
    const recorded = toPolledRecordedAiStep(
      {
        action: 'ai_script',
        source: 'ai',
        description: '等待状态变化后继续',
        prompt: '等待状态变化后继续',
        assistant_diagnostics: {
          segment_kind: 'read_only',
          stop_reason: 'goal_reached',
          recovery_attempts: 0,
        },
      },
      4,
      undefined,
    );

    expect(recorded.diagnostics).toEqual([
      'Segment kind: read_only',
      'Stop reason: goal_reached',
      'Recovery attempts: 0',
    ]);
    expect(recorded.locatorSummary).toBeUndefined();
  });
});

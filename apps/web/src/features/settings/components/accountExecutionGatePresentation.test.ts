import { describe, expect, it } from 'vitest';

import {
  accountExecutionGatePresentation,
  getBackupFreshness,
  getAccountExecutionGatePresentation,
  getSnapshotFreshness,
} from './accountExecutionGatePresentation';

describe('account execution gate presentation', () => {
  it('provides a user-facing label and explanation for every known gate', () => {
    expect(Object.keys(accountExecutionGatePresentation)).toHaveLength(17);

    Object.entries(accountExecutionGatePresentation).forEach(
      ([code, presentation]) => {
        expect(presentation.label).not.toBe(code);
        expect(presentation.label).not.toMatch(/^[A-Z0-9_]+$/);
        expect(presentation.passedDescription.length).toBeGreaterThan(6);
      }
    );
  });

  it('keeps an unknown contract value understandable', () => {
    expect(getAccountExecutionGatePresentation('FUTURE_GATE')).toEqual({
      label: '未识别的安全检查',
      passedDescription: '系统返回了尚未收录的门禁项。',
    });
  });

  it('counts down snapshot freshness from the server check time', () => {
    const now = Date.parse('2026-08-25T12:00:05Z');

    expect(getSnapshotFreshness(10, '2026-08-25T12:00:00', now)).toMatchObject({
      countdownLabel: '距过期 01:15',
      remainingSeconds: 75,
      tone: 'fresh',
    });
    expect(getSnapshotFreshness(70, '2026-08-25T12:00:00Z', now)).toMatchObject(
      {
        countdownLabel: '距过期 00:15',
        remainingSeconds: 15,
        tone: 'warning',
      }
    );
  });

  it('shows the backup countdown and warning window', () => {
    const now = Date.parse('2026-08-25T12:00:00Z');

    expect(getBackupFreshness('2026-08-24T16:00:00', now)).toMatchObject({
      countdownLabel: '距过期 04:00:00',
      remainingSeconds: 4 * 60 * 60,
      tone: 'fresh',
    });
    expect(getBackupFreshness('2026-08-24T13:00:00Z', now)).toMatchObject({
      countdownLabel: '距过期 01:00:00',
      remainingSeconds: 60 * 60,
      tone: 'warning',
    });
  });
});

import { describe, expect, it } from 'vitest';

import {
  createRollingDiagnosticRange,
  hasCandidateTraceIdentity,
  matchesDiagnosticVersion,
  SIGNAL_DIAGNOSTIC_WINDOW_MS,
  traceRelatedIdGroups,
} from './clientTrust';

describe('client trust helpers', () => {
  it('creates a twenty-day rolling diagnostic window', () => {
    const end = new Date('2026-08-23T08:00:00.000Z');
    const range = createRollingDiagnosticRange(end);

    expect(Date.parse(range.endTime)).toBe(end.getTime());
    expect(Date.parse(range.endTime) - Date.parse(range.startTime)).toBe(
      SIGNAL_DIAGNOSTIC_WINDOW_MS
    );
  });

  it('requires the complete account/run/candidate trace identity', () => {
    const trace = {
      accountId: 'account-1',
      strategyRunId: 'run-1',
      candidateId: 'candidate-1',
    };

    expect(hasCandidateTraceIdentity(trace, trace)).toBe(true);
    expect(
      hasCandidateTraceIdentity(trace, { ...trace, accountId: 'account-2' })
    ).toBe(false);
    expect(
      hasCandidateTraceIdentity(trace, { ...trace, strategyRunId: 'run-2' })
    ).toBe(false);
    expect(
      hasCandidateTraceIdentity(trace, { ...trace, candidateId: 'candidate-2' })
    ).toBe(false);
  });

  it('matches diagnostics only when policy, feature and profile all agree', () => {
    const partition = {
      policyVersion: 'policy-v3',
      featureSchemaVersion: 'feature-v3',
      profileVersion: 'profile-v1',
    };
    expect(
      matchesDiagnosticVersion({
        policyVersion: 'policy-v3',
        signalSnapshot: {
          featureSchemaVersion: 'feature-v3',
          profileVersion: 'profile-v1',
        },
      }, partition)
    ).toBe(true);
    expect(
      matchesDiagnosticVersion({
        policyVersion: 'policy-v3',
        signalSnapshot: {
          featureSchemaVersion: 'feature-v2',
          profileVersion: 'profile-v1',
        },
      }, partition)
    ).toBe(false);
    expect(
      matchesDiagnosticVersion({ policyVersion: 'policy-v3', signalSnapshot: null }, partition)
    ).toBe(false);
  });

  it('renders only bounded string related-id groups from plain objects', () => {
    expect(
      traceRelatedIdGroups({
        candidate_id: [' candidate-1 ', 42, 'candidate-2'],
        intent_id: 'intent-1',
        nested: { ignored: 'value' },
        empty: [],
      })
    ).toEqual([
      { key: 'candidate_id', ids: ['candidate-1', 'candidate-2'] },
      { key: 'intent_id', ids: ['intent-1'] },
    ]);
    expect(traceRelatedIdGroups(['not', 'an', 'object'])).toEqual([]);
    expect(traceRelatedIdGroups(new Date())).toEqual([]);
  });
});

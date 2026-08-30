import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTTradeReplayEvidence } from './useTTradeReplayEvidence';

type QueryKind = 'signal' | 'audit';
type Variables = {
  runId: string;
  backtestId: string;
  filters: Record<string, unknown>;
  first: number;
  after: string | null;
};
type Request = { query: string; variables: Variables; pause: boolean };
type Response = {
  fetching: boolean;
  operation?: { variables: Variables };
  data?: Record<string, unknown>;
  error?: { message: string };
};

const harness = vi.hoisted(() => ({
  requests: {} as Record<QueryKind, Request>,
  responses: {} as Record<QueryKind, Response>,
  refreshSignal: vi.fn(),
  refreshAudit: vi.fn(),
}));

vi.mock('@/generated/gql', () => ({
  gql: (source: string) => source,
  useFragment: (_fragment: unknown, value: unknown) => value,
}));
vi.mock('./useTTradeGlobal', () => ({
  TTradeSignalSnapshotFieldsFragment: 'snapshot-fragment',
}));
vi.mock('urql', () => ({
  useQuery: (request: Request) => {
    const kind = request.query.includes('Portfolio_ReplaySignals')
      ? 'signal'
      : 'audit';
    harness.requests[kind] = request;
    return [
      harness.responses[kind],
      kind === 'signal' ? harness.refreshSignal : harness.refreshAudit,
    ];
  },
}));

function respond(kind: QueryKind, ids: string[], cursor: string | null = null) {
  const variables = harness.requests[kind].variables;
  harness.responses[kind] = {
    fetching: false,
    operation: { variables },
    data: {
      [kind === 'signal'
        ? 'tTradeReplaySignalEvaluations'
        : 'tTradeReplayDecisionAudit']: {
        evidence: {
          runId: variables.runId,
          backtestId: variables.backtestId,
          backtestVersion: 1,
          availability: 'AVAILABLE',
          source: 'VERSION_ARCHIVE',
          sealed: true,
        },
        items: ids.map(id =>
          kind === 'signal'
            ? { id, eventKey: id, signalSnapshot: null }
            : { decision: { id }, evaluationEventKeys: [], executions: [] }
        ),
        summary:
          kind === 'signal'
            ? {
                eventCount: 237,
                candidateCount: 1,
                linkedIntentCount: 0,
                suppressedCount: 1,
              }
            : {
                decisionCount: 237,
                withIntentCount: 0,
                noIntentCount: 237,
                riskBlockedCount: 0,
              },
        pageInfo: { hasNextPage: Boolean(cursor), endCursor: cursor },
      },
    },
  };
}

const initialProps = {
  runId: 'run-1',
  backtestId: 'backtest-1',
  activeView: 'SIGNALS',
  includeDiagnostics: false,
};

beforeEach(() => {
  harness.responses.signal = { fetching: true };
  harness.responses.audit = { fetching: true };
  harness.refreshSignal.mockClear();
  harness.refreshAudit.mockClear();
});

describe('useTTradeReplayEvidence', () => {
  it('retains earlier pages while loading and deduplicates stable event keys', () => {
    const { result, rerender } = renderHook(useTTradeReplayEvidence, {
      initialProps,
    });
    respond('signal', ['event-1', 'event-2'], 'cursor-1');
    rerender(initialProps);

    act(() => result.current.loadMoreSignals());
    expect(harness.requests.signal.variables.after).toBe('cursor-1');
    expect(result.current.evaluations.map(item => item.eventKey)).toEqual([
      'event-1',
      'event-2',
    ]);
    expect(result.current.signalPage?.summary.eventCount).toBe(237);

    respond('signal', ['event-2', 'event-3']);
    rerender(initialProps);
    expect(result.current.evaluations.map(item => item.eventKey)).toEqual([
      'event-1',
      'event-2',
      'event-3',
    ]);
    expect(result.current.signalPage?.pageInfo.hasNextPage).toBe(false);

    act(() => result.current.refreshSignals());
    expect(harness.requests.signal.variables.after).toBeNull();
    expect(result.current.evaluations).toEqual([]);
  });

  it('never carries rows, cursors or trace filters into a different backtest version', () => {
    const { result, rerender } = renderHook(useTTradeReplayEvidence, {
      initialProps,
    });
    act(() => {
      result.current.setSignalFilters({ eventKey: 'exact-event' });
      result.current.setAuditFilters({ eventKey: 'exact-event' });
    });
    respond('signal', ['exact-event'], 'old-cursor');
    respond('audit', ['old-decision'], 'old-audit-cursor');
    rerender(initialProps);
    act(() => result.current.loadMoreSignals());

    rerender({ ...initialProps, backtestId: 'backtest-2' });
    expect(result.current.signalFilters).toEqual({});
    expect(result.current.auditFilters).toEqual({});
    expect(harness.requests.signal.variables.after).toBeNull();
    expect(result.current.evaluations).toEqual([]);
    expect(result.current.auditRecords).toEqual([]);
    expect(result.current.signalPage).toBeUndefined();
    expect(result.current.auditPage).toBeUndefined();
  });

  it('ignores late results and errors from a previous filter', () => {
    const { result, rerender } = renderHook(useTTradeReplayEvidence, {
      initialProps,
    });
    respond('signal', ['old-event']);
    rerender(initialProps);
    act(() => result.current.setSignalFilters({ candidateId: 'candidate-2' }));
    harness.responses.signal.error = { message: 'stale error' };
    rerender(initialProps);
    expect(result.current.evaluations).toEqual([]);
    expect(result.current.signalPage).toBeUndefined();
    expect(result.current.signalError).toBeUndefined();

    respond('signal', ['new-event']);
    rerender(initialProps);
    expect(result.current.evaluations.map(item => item.eventKey)).toEqual([
      'new-event',
    ]);
  });

  it('requests context in activity without converting audit decisions into signals', () => {
    const activityProps = { ...initialProps, activeView: 'EVENTS' };
    const { result, rerender } = renderHook(useTTradeReplayEvidence, {
      initialProps: activityProps,
    });
    expect(harness.requests.signal.variables.filters).toEqual({
      includeContext: true,
      includeDiagnostics: false,
    });
    expect(harness.requests.audit.pause).toBe(false);
    respond('signal', []);
    respond('audit', ['decision-only']);
    rerender(activityProps);
    expect(result.current.evaluations).toEqual([]);
    expect(result.current.auditRecords).toHaveLength(1);

    rerender({ ...activityProps, includeDiagnostics: true });
    expect(harness.requests.signal.variables.filters.includeDiagnostics).toBe(
      true
    );
    expect(result.current.evaluations).toEqual([]);
  });
});

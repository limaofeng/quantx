import * as React from 'react';
import { useQuery } from 'urql';

import { gql, useFragment as readFragment } from '@/generated/gql';
import type {
  Portfolio_ReplaySignalsQuery,
  Portfolio_ReplayAuditQuery,
  TTradeReplaySignalFilterInput,
  TTradeReplayAuditFilterInput,
} from '@/generated/gql/graphql';

import { TTradeSignalSnapshotFieldsFragment } from './useTTradeGlobal';

export const ReplaySignalsQuery = gql(`
  query Portfolio_ReplaySignals($runId: String!, $backtestId: String!, $filters: TTradeReplaySignalFilterInput, $first: Int!, $after: String) {
    tTradeReplaySignalEvaluations(runId: $runId, backtestId: $backtestId, filters: $filters, first: $first, after: $after) {
      evidence { runId backtestId backtestVersion availability source sealed reasonCode contentFingerprint }
      summary { eventCount candidateCount linkedIntentCount suppressedCount }
      pageInfo { hasNextPage endCursor }
      items {
        id eventKey category candidateId linkedIntentId accountId runId stockCode
        eventKind eventType evaluatedAt windowStartedAt windowEndedAt coalescedCount
        policyVersion schemaVersion contentFingerprint
        signalSnapshot { ...Portfolio_TTradeSignalSnapshotFields }
      }
    }
  }
`);

export const ReplayAuditQuery = gql(`
  query Portfolio_ReplayAudit($runId: String!, $backtestId: String!, $filters: TTradeReplayAuditFilterInput, $first: Int!, $after: String) {
    tTradeReplayDecisionAudit(runId: $runId, backtestId: $backtestId, filters: $filters, first: $first, after: $after) {
      evidence { runId backtestId backtestVersion availability source sealed reasonCode contentFingerprint }
      summary { decisionCount withIntentCount noIntentCount riskBlockedCount }
      pageInfo { hasNextPage endCursor }
      items {
        evaluationEventKeys
        decision {
          id instanceId traceId decidedAt inputSummary outputSummary statePatch decisionTrace reason tags
          tradeIntents { id traceId instrumentCode side targetBucket quantityIntent targetVolume targetAmount targetPositionPct priceIntent reason status createdAt updatedAt }
        }
        executions {
          id intentId instrumentCode side orderId riskDecision sizingResult orderStatus fillStatus
          executedPrice executedVolume executedTime reason traceId createdAt updatedAt
        }
      }
    }
  }
`);

type SignalPage = Portfolio_ReplaySignalsQuery['tTradeReplaySignalEvaluations'];
export type ReplayAuditPage =
  Portfolio_ReplayAuditQuery['tTradeReplayDecisionAudit'];
type SignalRecord = SignalPage['items'][number];
type AuditRecord = ReplayAuditPage['items'][number];

function useCursor<T>(scope: string, identity: (item: T) => string) {
  const [state, setState] = React.useState<{
    scope: string;
    after: string | null;
    prefix: T[];
  }>({ scope, after: null, prefix: [] });
  const after = state.scope === scope ? state.after : null;
  const merge = (items: T[]) => {
    const rows = new Map<string, T>();
    if (state.scope === scope && after)
      for (const item of state.prefix) rows.set(identity(item), item);
    for (const item of items) rows.set(identity(item), item);
    return [...rows.values()];
  };
  const reset = React.useCallback(
    () => setState({ scope, after: null, prefix: [] }),
    [scope]
  );
  const next = (items: T[], cursor?: string | null) => {
    if (cursor && cursor !== after)
      setState({ scope, after: cursor, prefix: items });
  };
  return {
    after,
    merge,
    reset,
    next,
    prefix: state.scope === scope ? state.prefix : [],
  };
}

const signalIdentity = (item: SignalRecord) => item.eventKey;
const auditIdentity = (item: AuditRecord) => item.decision.id;

type VersionFilters = {
  scope: string;
  signal: TTradeReplaySignalFilterInput;
  audit: TTradeReplayAuditFilterInput;
};

function useVersionFilters(scope: string) {
  const [state, setState] = React.useState<VersionFilters>({
    scope,
    signal: {},
    audit: {},
  });
  const current: VersionFilters =
    state.scope === scope ? state : { scope, signal: {}, audit: {} };
  const setSignalFilters = React.useCallback(
    (update: React.SetStateAction<TTradeReplaySignalFilterInput>) => {
      setState(previous => {
        const value =
          previous.scope === scope
            ? previous
            : { scope, signal: {}, audit: {} };
        return {
          ...value,
          signal: typeof update === 'function' ? update(value.signal) : update,
        };
      });
    },
    [scope]
  );
  const setAuditFilters = React.useCallback(
    (update: React.SetStateAction<TTradeReplayAuditFilterInput>) => {
      setState(previous => {
        const value =
          previous.scope === scope
            ? previous
            : { scope, signal: {}, audit: {} };
        return {
          ...value,
          audit: typeof update === 'function' ? update(value.audit) : update,
        };
      });
    },
    [scope]
  );
  return {
    signalFilters: current.signal,
    auditFilters: current.audit,
    setSignalFilters,
    setAuditFilters,
  };
}

export function useTTradeReplayEvidence({
  runId,
  backtestId,
  activeView,
  includeDiagnostics,
}: {
  runId: string;
  backtestId?: string | null;
  activeView: string;
  includeDiagnostics: boolean;
}) {
  const { signalFilters, auditFilters, setSignalFilters, setAuditFilters } =
    useVersionFilters(JSON.stringify([runId, backtestId]));
  const effectiveSignalFilters =
    activeView === 'EVENTS'
      ? { includeContext: true, includeDiagnostics }
      : signalFilters;
  const effectiveAuditFilters = activeView === 'EVENTS' ? {} : auditFilters;
  const signalScope = JSON.stringify([
    runId,
    backtestId,
    effectiveSignalFilters,
  ]);
  const auditScope = JSON.stringify([runId, backtestId, effectiveAuditFilters]);
  const signalsCursor = useCursor<SignalRecord>(signalScope, signalIdentity);
  const auditCursor = useCursor<AuditRecord>(auditScope, auditIdentity);
  const [signalsResult, reexecuteSignals] = useQuery({
    query: ReplaySignalsQuery,
    variables: {
      runId,
      backtestId: backtestId || '',
      filters: effectiveSignalFilters,
      first: 50,
      after: signalsCursor.after,
    },
    pause: !runId || !backtestId || !['SIGNALS', 'EVENTS'].includes(activeView),
    requestPolicy: 'cache-and-network',
  });
  const [auditResult, reexecuteAudit] = useQuery({
    query: ReplayAuditQuery,
    variables: {
      runId,
      backtestId: backtestId || '',
      filters: effectiveAuditFilters,
      first: 50,
      after: auditCursor.after,
    },
    pause: !runId || !backtestId || !['AUDIT', 'EVENTS'].includes(activeView),
    requestPolicy: 'cache-and-network',
  });
  const signalResponse = signalsResult.data?.tTradeReplaySignalEvaluations;
  const auditResponse = auditResult.data?.tTradeReplayDecisionAudit;
  const signalRequestMatches =
    signalsResult.operation?.variables.runId === runId &&
    signalsResult.operation.variables.backtestId === backtestId &&
    JSON.stringify(signalsResult.operation?.variables.filters) ===
      JSON.stringify(effectiveSignalFilters) &&
    signalsResult.operation?.variables.after === signalsCursor.after;
  const auditRequestMatches =
    auditResult.operation?.variables.runId === runId &&
    auditResult.operation.variables.backtestId === backtestId &&
    JSON.stringify(auditResult.operation?.variables.filters) ===
      JSON.stringify(effectiveAuditFilters) &&
    auditResult.operation?.variables.after === auditCursor.after;
  const signalMatches =
    signalRequestMatches &&
    signalResponse?.evidence.runId === runId &&
    signalResponse.evidence.backtestId === backtestId;
  const auditMatches =
    auditRequestMatches &&
    auditResponse?.evidence.runId === runId &&
    auditResponse.evidence.backtestId === backtestId;
  const lastSignalPage = React.useRef<{ scope: string; page: SignalPage }>();
  const lastAuditPage = React.useRef<{
    scope: string;
    page: ReplayAuditPage;
  }>();
  if (signalMatches && signalResponse)
    lastSignalPage.current = { scope: signalScope, page: signalResponse };
  if (auditMatches && auditResponse)
    lastAuditPage.current = { scope: auditScope, page: auditResponse };
  const signalPage =
    lastSignalPage.current?.scope === signalScope
      ? lastSignalPage.current.page
      : undefined;
  const auditPage =
    lastAuditPage.current?.scope === auditScope
      ? lastAuditPage.current.page
      : undefined;
  const signalRecords =
    signalMatches && signalResponse
      ? signalsCursor.merge(signalResponse.items)
      : signalsCursor.prefix;
  const auditRecords =
    auditMatches && auditResponse
      ? auditCursor.merge(auditResponse.items)
      : auditCursor.prefix;
  const evaluations = signalRecords.map(item => ({
    ...item,
    signalSnapshot: readFragment(
      TTradeSignalSnapshotFieldsFragment,
      item.signalSnapshot
    ),
  }));
  const { reset: resetSignals, after: signalsAfter } = signalsCursor;
  const { reset: resetAudit, after: auditAfter } = auditCursor;
  const refreshSignals = React.useCallback(() => {
    resetSignals();
    if (!signalsAfter) reexecuteSignals({ requestPolicy: 'network-only' });
  }, [signalsAfter, resetSignals, reexecuteSignals]);
  const refreshAudit = React.useCallback(() => {
    resetAudit();
    if (!auditAfter) reexecuteAudit({ requestPolicy: 'network-only' });
  }, [auditAfter, resetAudit, reexecuteAudit]);
  const refresh = React.useCallback(() => {
    refreshSignals();
    refreshAudit();
  }, [refreshSignals, refreshAudit]);

  return {
    evaluations,
    auditRecords,
    signalPage,
    auditPage,
    signalFilters,
    auditFilters,
    setSignalFilters,
    setAuditFilters,
    refresh,
    refreshSignals,
    refreshAudit,
    signalsLoading: signalsResult.fetching,
    auditLoading: auditResult.fetching,
    signalError: signalRequestMatches
      ? signalsResult.error?.message
      : undefined,
    auditError: auditRequestMatches ? auditResult.error?.message : undefined,
    loadMoreSignals: () =>
      signalsCursor.next(signalRecords, signalPage?.pageInfo.endCursor),
    loadMoreAudit: () =>
      auditCursor.next(auditRecords, auditPage?.pageInfo.endCursor),
  };
}

export type ReplayEvidenceController = ReturnType<
  typeof useTTradeReplayEvidence
>;
export type ReplaySignal = ReplayEvidenceController['evaluations'][number];

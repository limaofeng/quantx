/** In-memory browser fixtures only. No API, database, account or strategy runs. */
import type {
  Portfolio_ReplayAuditQuery,
  Portfolio_ReplaySignalsQuery,
  TTradeReplayAuditFilterInput,
  TTradeReplaySignalFilterInput,
} from '@/generated/gql/graphql';
import {
  TTradeReplayEvidenceAvailability,
  TTradeReplayEvidenceSource,
  TTradeSignalEvaluationKind,
} from '@/generated/gql/graphql';

type SignalPage = Portfolio_ReplaySignalsQuery['tTradeReplaySignalEvaluations'];
type AuditPage = Portfolio_ReplayAuditQuery['tTradeReplayDecisionAudit'];
type Variables = {
  runId: string;
  backtestId: string;
  filters: TTradeReplaySignalFilterInput & TTradeReplayAuditFilterInput;
  first: number;
  after?: string | null;
};

export type FixtureRequest = {
  kind: 'signal' | 'audit';
  version: string;
  after: string | null;
  filters: Variables['filters'];
};

function records(variables: Variables): SignalPage['items'] {
  const version = variables.backtestId.endsWith('B') ? 'B' : 'A';
  const count = version === 'A' ? 125 : 17;
  return Array.from({ length: count + 1 }, (_, index) => {
    const context = index === count;
    const number = String(index + 1).padStart(3, '0');
    return {
      id: `sample-${version}-${number}`,
      eventKey: context
        ? `sample-${version}-policy`
        : `sample-${version}-event-${number}`,
      category: context ? 'CONTEXT' : 'SIGNAL',
      candidateId: context
        ? null
        : `sample-${version}-candidate-${Math.floor(index / 5)}`,
      linkedIntentId: null,
      accountId: 'browser-fixture-only',
      runId: variables.runId,
      stockCode: index % 2 ? '000001.SZ' : '600000.SH',
      eventKind: TTradeSignalEvaluationKind.Material,
      eventType: context ? 'POLICY_CHANGED' : 'CANDIDATE_SUPPRESSED',
      evaluatedAt: new Date(
        Date.UTC(2026, 7, 31, 1, 30, count - index)
      ).toISOString(),
      windowStartedAt: null,
      windowEndedAt: null,
      coalescedCount: 1,
      policyVersion: 'browser-fixture',
      schemaVersion: '3',
      contentFingerprint: `sample-fingerprint-${version}-${number}`,
      signalSnapshot: null,
    };
  });
}

function evidence(variables: Variables): SignalPage['evidence'] {
  return {
    runId: variables.runId,
    backtestId: variables.backtestId,
    backtestVersion: variables.backtestId.endsWith('B') ? 2 : 1,
    availability: TTradeReplayEvidenceAvailability.Available,
    source: TTradeReplayEvidenceSource.VersionArchive,
    sealed: true,
    reasonCode: null,
    contentFingerprint: 'browser-fixture-not-a-real-archive',
  };
}

function page<T>(rows: T[], variables: Variables) {
  const offset = Number(variables.after?.split(':').pop() || 0);
  const end = offset + variables.first;
  return {
    items: rows.slice(offset, end),
    pageInfo: {
      hasNextPage: end < rows.length,
      endCursor: end < rows.length ? `${variables.backtestId}:${end}` : null,
    },
  };
}

export function fixtureSignals(variables: Variables): SignalPage {
  const filters = variables.filters;
  const search = filters.search?.toLowerCase();
  const rows = records(variables).filter(
    item =>
      (filters.includeContext || item.category === 'SIGNAL') &&
      (!filters.eventKey || item.eventKey === filters.eventKey) &&
      (!filters.candidateId || item.candidateId === filters.candidateId) &&
      (!filters.stockCode || item.stockCode === filters.stockCode) &&
      (!search ||
        `${item.stockCode} ${item.eventKey} ${item.eventType}`
          .toLowerCase()
          .includes(search))
  );
  return {
    evidence: evidence(variables),
    ...page(rows, variables),
    summary: {
      eventCount: rows.length,
      candidateCount: new Set(
        rows.flatMap(row => (row.candidateId ? [row.candidateId] : []))
      ).size,
      linkedIntentCount: 0,
      suppressedCount: rows.filter(row => row.category === 'SIGNAL').length,
    },
  };
}

export function fixtureAudit(variables: Variables): AuditPage {
  const filters = variables.filters;
  const search = filters.search?.toLowerCase();
  // Reverse ordering puts linked evidence outside the first audit page.
  const rows: AuditPage['items'] = records(variables)
    .reverse()
    .map(item => ({
      evaluationEventKeys: [item.eventKey],
      executions: [],
      decision: {
        id: `decision-${item.id}`,
        instanceId: variables.runId,
        traceId: `trace-${item.id}`,
        decidedAt: item.evaluatedAt,
        inputSummary: {
          instrument_code: item.stockCode,
          fixture: '非业务数据',
        },
        outputSummary: { trade_intent_count: 0 },
        statePatch: {},
        decisionTrace: {},
        reason: 'MINIMUM_COVERAGE_NOT_REACHED',
        tags: [],
        tradeIntents: [],
      },
    }))
    .filter(
      item =>
        (!filters.eventKey ||
          item.evaluationEventKeys.includes(filters.eventKey)) &&
        (!filters.stockCode ||
          item.decision.inputSummary.instrument_code === filters.stockCode) &&
        filters.withIntent !== true &&
        (!search ||
          `${item.decision.inputSummary.instrument_code} ${item.evaluationEventKeys[0]}`
            .toLowerCase()
            .includes(search))
    );
  return {
    evidence: evidence(variables),
    ...page(rows, variables),
    summary: {
      decisionCount: rows.length,
      withIntentCount: 0,
      noIntentCount: rows.length,
      riskBlockedCount: 0,
    },
  };
}

export function fixtureFetch(
  onRequest: (request: FixtureRequest) => void
): typeof fetch {
  return async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as {
      query: string;
      variables: Variables;
    };
    const kind = request.query.includes('Portfolio_ReplaySignals')
      ? 'signal'
      : 'audit';
    if (
      !request.query.includes(
        kind === 'signal' ? 'Portfolio_ReplaySignals' : 'Portfolio_ReplayAudit'
      )
    ) {
      throw new Error('Browser fixture refuses unknown operations');
    }
    // The only transport is this local function; never forward to window.fetch.
    await new Promise(resolve => setTimeout(resolve, 80));
    onRequest({
      kind,
      version: request.variables.backtestId,
      after: request.variables.after || null,
      filters: request.variables.filters,
    });
    const data =
      kind === 'signal'
        ? { tTradeReplaySignalEvaluations: fixtureSignals(request.variables) }
        : { tTradeReplayDecisionAudit: fixtureAudit(request.variables) };
    return new Response(JSON.stringify({ data }), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
}

import type { SignalSnapshot } from './monitoring';

export type ActivitySignalEvaluation = {
  id: string;
  accountId: string;
  runId: string;
  stockCode: string;
  eventKind: string;
  eventType: string;
  evaluatedAt: string;
  coalescedCount: number;
  policyVersion: string;
  signalSnapshot?: SignalSnapshot | null;
};

export type ActivityBatchEvent = {
  eventId: string;
  batchId: string;
  eventType: string;
  status: string;
  clientOrderId: string;
  brokerOrderId?: string | null;
  payload: unknown;
  createdAt: string;
  appliedAt?: string | null;
  error?: string | null;
};

export type ActivityBatch = {
  batchId: string;
  stockCode: string;
  strategyRunId: string;
  status: string;
  entryIntentId?: string | null;
  exitIntentId?: string | null;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  lastPrice: number;
  lastNetProfitPct: number;
  peakNetProfitPct: number;
  trailingFloorPct?: number | null;
  exitReason?: string | null;
  exceptionReason?: string | null;
  version: number;
  updatedAt?: string | null;
};

export type ActivityKind =
  'SIGNAL' | 'CANDIDATE' | 'DIAGNOSTIC' | 'ORDER' | 'TRADE' | 'ERROR';

export type ActivityTone =
  'blue' | 'emerald' | 'amber' | 'rose' | 'slate' | 'marketBuy' | 'marketSell';

export type ExecutionSnapshot = {
  report: Readonly<Record<string, unknown>>;
  metadata: Readonly<Record<string, unknown>>;
  stockCode: string;
  direction: 'BUY' | 'SELL' | 'UNKNOWN';
  role: string;
  orderStatus: string;
  orderVolume: number | null;
  tradedVolume: number | null;
  price: number | null;
  tradedPrice: number | null;
  reportTime: string | null;
  executionId: string | null;
  reportSequence: string | null;
};

export type TTradeActivityItem = {
  id: string;
  occurredAt: string;
  stockCode: string;
  kind: ActivityKind;
  tone: ActivityTone;
  eventType: string;
  title: string;
  summary: string;
  searchableText: string;
  signalEvaluation?: ActivitySignalEvaluation;
  previousSignalSnapshot?: SignalSnapshot | null;
  batchEvent?: ActivityBatchEvent;
  executionSnapshot?: ExecutionSnapshot;
  batch?: ActivityBatch;
};

export type ActivityFilter = {
  includeDiagnostics: boolean;
  kind: 'ALL' | ActivityKind;
  stockCode: string;
  search: string;
};

const CANDIDATE_EVENT_TYPES = new Set([
  'CANDIDATE_LATCHED',
  'CANDIDATE_AWAITING_APPROVAL',
  'CANDIDATE_SUPPRESSED',
  'CANDIDATE_REARMING',
  'CANDIDATE_CLEARED',
  'CANDIDATE_STATE_CHANGED',
  'INTENT_LINKED',
]);

const signalTitles: Readonly<Record<string, string>> = {
  FSM_TRANSITION: 'FSM 状态变更',
  CANDIDATE_LATCHED: '候选已锁存',
  CANDIDATE_AWAITING_APPROVAL: '候选等待确认',
  CANDIDATE_SUPPRESSED: '候选已抑制',
  CANDIDATE_REARMING: '候选等待再武装',
  CANDIDATE_CLEARED: '候选已清除',
  CANDIDATE_STATE_CHANGED: '候选状态变更',
  INTENT_LINKED: '交易意图已关联',
  CONTINUITY_GENERATION_CHANGED: '行情连续代际变更',
  POLICY_CHANGED: '信号策略已变更',
  PROFILE_CHANGED: '标的画像已变更',
  COALESCED_DIAGNOSTIC: '诊断观测',
};

function recordValue(value: unknown): Readonly<Record<string, unknown>> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Readonly<Record<string, unknown>>)
    : {};
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
      return String(value);
  }
  return '';
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function directionFrom(
  report: Readonly<Record<string, unknown>>,
  metadata: Readonly<Record<string, unknown>>
): ExecutionSnapshot['direction'] {
  const role = firstText(metadata.t_trade_role, metadata.role).toUpperCase();
  if (role === 'ENTRY') return 'BUY';
  if (role === 'EXIT') return 'SELL';

  const direction = firstText(
    report.direction,
    report.side,
    report.order_direction
  ).toUpperCase();
  if (['BUY', 'B', '23'].includes(direction)) return 'BUY';
  if (['SELL', 'S', '24'].includes(direction)) return 'SELL';

  const orderType = firstNumber(report.order_type, report.orderType);
  if (orderType === 23) return 'BUY';
  if (orderType === 24) return 'SELL';
  return 'UNKNOWN';
}

function normalizeExecutionSnapshot(
  event: ActivityBatchEvent,
  batch?: ActivityBatch
): ExecutionSnapshot {
  const payload = recordValue(event.payload);
  const report = recordValue(payload.report);
  const metadata = recordValue(payload.metadata);
  return {
    report,
    metadata,
    stockCode: firstText(
      report.stock_code,
      report.instrument_code,
      metadata.instrument_code,
      batch?.stockCode
    ).toUpperCase(),
    direction: directionFrom(report, metadata),
    role: firstText(metadata.t_trade_role, metadata.role).toUpperCase(),
    orderStatus: firstText(
      report.effective_order_status,
      report.order_status,
      report.status,
      event.status
    ).toUpperCase(),
    orderVolume: firstNumber(report.order_volume, report.volume),
    tradedVolume: firstNumber(
      report.traded_volume,
      report.filled_volume,
      report.volume
    ),
    price: firstNumber(report.price, report.order_price),
    tradedPrice: firstNumber(
      report.traded_price,
      report.filled_price,
      report.price
    ),
    reportTime:
      firstText(
        report.traded_time,
        report.order_time,
        report.timestamp,
        report.reported_at
      ) || null,
    executionId:
      firstText(report.execution_id, report.trade_id, report.executionId) ||
      null,
    reportSequence:
      firstText(
        report.source_sequence,
        report.sequence,
        payload.source_sequence,
        payload.sequence
      ) || null,
  };
}

function formatCompactNumber(value: number | null, digits = 2) {
  if (value == null) return '--';
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function signalSummary(evaluation: ActivitySignalEvaluation): string {
  const snapshot = evaluation.signalSnapshot;
  if (!snapshot) return evaluation.eventType;
  const score =
    snapshot.opportunityScore == null
      ? '机会分不可计算'
      : `机会分 ${formatCompactNumber(snapshot.opportunityScore, 1)} / ${formatCompactNumber(snapshot.candidateThreshold, 0)}`;
  const blocker = snapshot.topBlockers[0]?.label;
  return [score, blocker ? `首要阻断：${blocker}` : null]
    .filter(Boolean)
    .join(' · ');
}

function executionSummary(
  snapshot: ExecutionSnapshot,
  eventType: string
): string {
  const direction =
    snapshot.direction === 'BUY'
      ? '买入'
      : snapshot.direction === 'SELL'
        ? '卖出'
        : '方向未知';
  if (eventType.toUpperCase() === 'TRADE') {
    return `${direction} ${formatCompactNumber(snapshot.tradedVolume, 0)} 股 @ ${formatCompactNumber(snapshot.tradedPrice)}`;
  }
  const volume = snapshot.orderVolume ?? snapshot.tradedVolume;
  return [
    direction,
    volume == null ? null : `${formatCompactNumber(volume, 0)} 股`,
    snapshot.orderStatus || null,
  ]
    .filter(Boolean)
    .join(' · ');
}

function signalKind(evaluation: ActivitySignalEvaluation): ActivityKind {
  if (evaluation.eventKind === 'COALESCED_DIAGNOSTIC') return 'DIAGNOSTIC';
  if (CANDIDATE_EVENT_TYPES.has(evaluation.eventType)) return 'CANDIDATE';
  return 'SIGNAL';
}

function signalTone(evaluation: ActivitySignalEvaluation): ActivityTone {
  if (evaluation.eventKind === 'COALESCED_DIAGNOSTIC') return 'slate';
  if (evaluation.eventType.includes('SUPPRESSED')) return 'rose';
  if (
    evaluation.eventType.includes('AWAITING') ||
    evaluation.eventType.includes('REARMING') ||
    evaluation.eventType.includes('POLICY') ||
    evaluation.eventType.includes('PROFILE') ||
    evaluation.eventType.includes('CONTINUITY')
  ) {
    return 'amber';
  }
  return 'blue';
}

function executionKind(event: ActivityBatchEvent): ActivityKind {
  if (event.error || event.status.toUpperCase() === 'FAILED') return 'ERROR';
  return event.eventType.toUpperCase() === 'TRADE' ? 'TRADE' : 'ORDER';
}

function executionTone(
  kind: ActivityKind,
  snapshot: ExecutionSnapshot
): ActivityTone {
  if (kind === 'ERROR') return 'rose';
  if (kind === 'ORDER') return 'slate';
  if (snapshot.direction === 'BUY') return 'marketBuy';
  if (snapshot.direction === 'SELL') return 'marketSell';
  return 'emerald';
}

function epoch(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function buildTTradeActivityItems(
  evaluations: readonly ActivitySignalEvaluation[],
  events: readonly ActivityBatchEvent[],
  batches: readonly ActivityBatch[]
): TTradeActivityItem[] {
  const batchById = new Map(batches.map(batch => [batch.batchId, batch]));
  const sortedEvaluations = [...evaluations].sort(
    (left, right) => epoch(right.evaluatedAt) - epoch(left.evaluatedAt)
  );
  const previousById = new Map<string, SignalSnapshot | null>();
  const lastSnapshotByRunAndStock = new Map<string, SignalSnapshot>();
  for (let index = sortedEvaluations.length - 1; index >= 0; index -= 1) {
    const evaluation = sortedEvaluations[index];
    const stockCode = evaluation.stockCode.toUpperCase();
    const snapshotKey = `${evaluation.runId}:${stockCode}`;
    previousById.set(
      evaluation.id,
      lastSnapshotByRunAndStock.get(snapshotKey) || null
    );
    if (evaluation.signalSnapshot) {
      lastSnapshotByRunAndStock.set(snapshotKey, evaluation.signalSnapshot);
    }
  }

  const signalItems: TTradeActivityItem[] = sortedEvaluations.map(
    evaluation => {
      const kind = signalKind(evaluation);
      const title =
        signalTitles[evaluation.eventType] ||
        (kind === 'DIAGNOSTIC' ? '诊断观测' : evaluation.eventType);
      const summary = signalSummary(evaluation);
      return {
        id: `signal:${evaluation.id}`,
        occurredAt: evaluation.evaluatedAt,
        stockCode: evaluation.stockCode.toUpperCase(),
        kind,
        tone: signalTone(evaluation),
        eventType: evaluation.eventType,
        title,
        summary,
        searchableText: [
          evaluation.stockCode,
          evaluation.eventType,
          title,
          summary,
          evaluation.runId,
          evaluation.signalSnapshot?.candidateId,
          evaluation.signalSnapshot?.pendingEntryIntentId,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase(),
        signalEvaluation: evaluation,
        previousSignalSnapshot: previousById.get(evaluation.id),
      };
    }
  );

  const executionItems: TTradeActivityItem[] = events.map(event => {
    const batch = batchById.get(event.batchId);
    const snapshot = normalizeExecutionSnapshot(event, batch);
    const kind = executionKind(event);
    const title =
      kind === 'TRADE'
        ? '真实成交'
        : kind === 'ERROR'
          ? '执行事件异常'
          : '委托状态';
    const summary = executionSummary(snapshot, event.eventType);
    return {
      id: `execution:${event.eventId}`,
      occurredAt: event.createdAt,
      stockCode: snapshot.stockCode,
      kind,
      tone: executionTone(kind, snapshot),
      eventType: event.eventType,
      title,
      summary,
      searchableText: [
        snapshot.stockCode,
        event.eventType,
        event.status,
        event.eventId,
        event.batchId,
        event.clientOrderId,
        event.brokerOrderId,
        snapshot.executionId,
        title,
        summary,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase(),
      batchEvent: event,
      executionSnapshot: snapshot,
      batch,
    };
  });

  return [...signalItems, ...executionItems].sort((left, right) => {
    const timeOrder = epoch(right.occurredAt) - epoch(left.occurredAt);
    return timeOrder || right.id.localeCompare(left.id);
  });
}

export function filterTTradeActivityItems(
  items: readonly TTradeActivityItem[],
  filter: ActivityFilter
): TTradeActivityItem[] {
  const query = filter.search.trim().toLowerCase();
  const stockCode = filter.stockCode.trim().toUpperCase();
  return items.filter(item => {
    if (!filter.includeDiagnostics && item.kind === 'DIAGNOSTIC') return false;
    if (filter.kind !== 'ALL' && item.kind !== filter.kind) return false;
    if (stockCode && stockCode !== 'ALL' && item.stockCode !== stockCode) {
      return false;
    }
    return !query || item.searchableText.includes(query);
  });
}

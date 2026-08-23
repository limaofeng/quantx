import { isTradingHours } from '@/shared/utils/date';

export const DATA_HEALTH_VALUES = [
  'WARMING',
  'READY',
  'DEGRADED',
  'STALE',
  'CONTINUITY_LOST',
  'INSUFFICIENT',
] as const;

export const PULLBACK_PHASE_VALUES = [
  'OBSERVING',
  'PULLBACK_FORMING',
  'LOW_STABILIZING',
  'REBOUND_CONFIRMING',
  'CANDIDATE_LATCHED',
  'SUPPRESSED',
] as const;

export const MOMENTUM_PHASE_VALUES = [
  'OBSERVING',
  'BASELINING',
  'MOMENTUM_BUILDING',
  'ACCELERATING',
  'OVEREXTENDED',
  'CANDIDATE_LATCHED',
  'SUPPRESSED',
] as const;

export const CANDIDATE_STATUS_VALUES = [
  'NONE',
  'LATCHED',
  'AWAITING_APPROVAL',
  'SUPPRESSED',
  'REARMING',
] as const;

export const SIGNAL_PATH_VALUES = [
  'PULLBACK_REBOUND',
  'MOMENTUM_ACCELERATION',
] as const;

// Keep this list aligned with the published TTradeDominantPhase enum. The
// GraphQL scalar is currently represented as a string in this hand-written
// client shape, so trust must not be based on a partial label map.
export const DOMINANT_PHASE_VALUES = [
  'NONE',
  'PULLBACK_OBSERVING',
  'PULLBACK_FORMING',
  'PULLBACK_LOW_STABILIZING',
  'PULLBACK_REBOUND_CONFIRMING',
  'PULLBACK_CANDIDATE_LATCHED',
  'PULLBACK_SUPPRESSED',
  'MOMENTUM_OBSERVING',
  'MOMENTUM_BASELINING',
  'MOMENTUM_BUILDING',
  'MOMENTUM_ACCELERATING',
  'MOMENTUM_OVEREXTENDED',
  'MOMENTUM_CANDIDATE_LATCHED',
  'MOMENTUM_SUPPRESSED',
] as const;

export const SIGNAL_STATE_SCHEMA_VERSION = '3';
export const SIGNAL_FEATURE_SCHEMA_VERSION = '1';

export const T_TRADE_CLIENT_TELEMETRY_EVENTS = [
  'REFRESH_SUCCESS',
  'REFRESH_FAILURE',
  'SUBSCRIPTION_RECONNECTED',
] as const;

export type TTradeClientTelemetryEvent =
  (typeof T_TRADE_CLIENT_TELEMETRY_EVENTS)[number];

export type SignalSnapshotRefreshCoordinator = ReturnType<
  typeof createSignalSnapshotRefreshCoordinator
>;

/**
 * Serializes monitor refreshes and binds trust to the epoch that requested
 * them. URQL's hook reexecute function is fire-and-forget and operations with
 * the same key may be deduplicated, so a newer reconnect waits for the prior
 * request to finish before it starts its own network-only request.
 */
export function createSignalSnapshotRefreshCoordinator() {
  let currentEpoch = 0;
  let currentAccountId: string | null = null;
  let trustedEpoch: number | null = null;
  let inFlight: Promise<boolean> | null = null;

  const beginEpoch = (accountId: string | null | undefined) => {
    currentEpoch += 1;
    currentAccountId = accountId || null;
    trustedEpoch = null;
    return currentEpoch;
  };

  const isCurrent = (epoch: number, accountId: string) =>
    epoch === currentEpoch && accountId === currentAccountId;

  const isTrusted = (accountId: string) =>
    trustedEpoch === currentEpoch && accountId === currentAccountId;

  const refresh = async (
    epoch: number,
    accountId: string,
    request: () => PromiseLike<boolean>
  ): Promise<boolean> => {
    const previous = inFlight;
    if (previous) await previous.catch(() => false);

    // A disconnect, account change, or newer reconnect invalidates the work
    // that was waiting behind the previous request.
    if (!isCurrent(epoch, accountId)) return false;

    let requestPromise: Promise<boolean>;
    try {
      requestPromise = Promise.resolve().then(request);
    } catch {
      return false;
    }
    inFlight = requestPromise;
    try {
      const succeeded = await requestPromise;
      if (!succeeded || !isCurrent(epoch, accountId)) return false;
      trustedEpoch = epoch;
      return true;
    } catch {
      return false;
    } finally {
      if (inFlight === requestPromise) inFlight = null;
    }
  };

  return {
    beginEpoch,
    currentEpoch: () => currentEpoch,
    isCurrent,
    isTrusted,
    refresh,
  };
}

export function createTTradeClientTelemetryReporter(
  send: (event: TTradeClientTelemetryEvent) => PromiseLike<unknown> | unknown,
  options: { now?: () => number; throttleMs?: number } = {}
) {
  const now = options.now ?? Date.now;
  const throttleMs = Math.max(0, options.throttleMs ?? 30_000);
  const allowedEvents = new Set<string>(T_TRADE_CLIENT_TELEMETRY_EVENTS);
  const lastSentAt = new Map<TTradeClientTelemetryEvent, number>();
  const inFlight = new Set<TTradeClientTelemetryEvent>();

  return (event: TTradeClientTelemetryEvent): boolean => {
    if (!allowedEvents.has(event) || inFlight.has(event)) return false;
    const timestamp = now();
    const previous = lastSentAt.get(event);
    if (previous != null && timestamp - previous < throttleMs) return false;
    lastSentAt.set(event, timestamp);
    inFlight.add(event);
    void Promise.resolve()
      .then(() => send(event))
      .catch(() => undefined)
      .finally(() => inFlight.delete(event));
    return true;
  };
}

export type SignalReason = {
  code: string;
  label: string;
  detail: string;
};

export type SignalGate = {
  code: string;
  label: string;
  passed: boolean;
  observedValue?: number | null;
  requiredValue?: number | null;
  detail: string;
};

export type ScoreContribution = {
  code: string;
  label: string;
  points: number;
  maxPoints: number;
  observedValue?: number | null;
  targetValue?: number | null;
  detail: string;
};

export type SignalBlocker = {
  code: string;
  label: string;
  detail: string;
};

export type SignalFeatures = {
  sampleCount: number;
  coverageSeconds?: number | null;
  maxGapSeconds?: number | null;
  price?: number | null;
  priceTick?: number | null;
  bidPrice?: number | null;
  askPrice?: number | null;
  spreadTicks?: number | null;
  spreadPct?: number | null;
  bookImbalance?: number | null;
  sessionVwap?: number | null;
  vwapPremiumPct?: number | null;
  windowHigh?: number | null;
  windowLow?: number | null;
  pullbackPct?: number | null;
  reboundPct?: number | null;
  secondsSinceLow?: number | null;
  reboundSlopePctPerSecond?: number | null;
  rangePosition?: number | null;
  momentumRisePct?: number | null;
  momentumMoveSeconds?: number | null;
  momentumWindowHigh?: number | null;
  momentumRangePosition?: number | null;
  momentumBaselineCoverageSeconds?: number | null;
  momentumAmountVelocityRatio?: number | null;
};

export type SignalBranch = {
  phase: string;
  score?: number | null;
  preview: boolean;
  candidateReady: boolean;
  hardGates: readonly SignalGate[];
  scoreContributions: readonly ScoreContribution[];
  blockers: readonly SignalBlocker[];
};

export type SignalSnapshot = {
  instrumentCode: string;
  tradeDate: string;
  evaluatedAt: string;
  sourceAt: string;
  sourceTimeMs: string;
  tickOrdinal: string;
  continuityGeneration: string;
  dataAgeMs?: number | null;
  windowCoverageSeconds?: number | null;
  sampleCount: number;
  dataHealth: string;
  dataHealthReasons: readonly SignalReason[];
  pullbackPhase: string;
  momentumPhase: string;
  dominantPhase: string;
  selectedPath?: string | null;
  pullbackScore?: number | null;
  momentumScore?: number | null;
  opportunityScore?: number | null;
  previewThreshold: number;
  candidateThreshold: number;
  revalidateThreshold: number;
  rearmThreshold: number;
  features: SignalFeatures;
  pullback: SignalBranch;
  momentum: SignalBranch;
  hardGates: readonly SignalGate[];
  scoreContributions: readonly ScoreContribution[];
  topBlockers: readonly SignalBlocker[];
  episodeId?: string | null;
  candidateId?: string | null;
  candidateFingerprint?: string | null;
  candidateStatus: string;
  candidateCreatedAt?: string | null;
  candidateExpiresAt?: string | null;
  pendingEntryIntentId?: string | null;
  signalVersion: number;
  candidateStateVersion: number;
  stateSchemaVersion: string;
  featureSchemaVersion: string;
  policyVersion: string;
  configVersion: number;
  profileVersion?: string | null;
  profileFingerprint?: string | null;
};

export type MonitorSession = {
  runId: string;
  stockCode?: string;
  runStatus: string;
  status: string;
  mode: string;
  pendingEntryIntentId?: string | null;
  pendingExitIntentId?: string | null;
  entryOrderStatus: string;
  exitOrderStatus: string;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  lastPrice?: number;
  lastNetProfitPct: number;
  peakNetProfitPct: number;
  trailingFloorPct?: number | null;
  targetProfitPct?: number;
  profitArmed: boolean;
  lastExitReason: string;
  completedCycles: number;
  canCancel: boolean;
  errorMessage?: string | null;
  plannedEntryAmount?: number;
  signalSnapshot?: SignalSnapshot | null;
};

export type MonitorHolding = {
  stockCode: string;
  instrumentName: string;
  volume: number;
  availableVolume: number;
  ignored: boolean;
  eligible: boolean;
  status: string;
  reason: string;
  session?: MonitorSession | null;
};

export type FreshnessLevel =
  'LIVE' | 'DELAYED' | 'STALE' | 'CLOSED' | 'MISSING';

export type Freshness = {
  ageSeconds: number | null;
  label: string;
  level: FreshnessLevel;
};

export type AttentionRow<TQuote = unknown> = {
  attentionLevel: number;
  distanceToCandidate: number | null;
  holding: MonitorHolding;
  quote?: TQuote;
  session?: MonitorSession | null;
  snapshot?: SignalSnapshot | null;
};

const includes = (values: readonly string[], candidate: unknown): boolean =>
  typeof candidate === 'string' && values.includes(candidate);

export function isKnownSignalSnapshot(
  snapshot: SignalSnapshot | null | undefined
): snapshot is SignalSnapshot {
  if (!snapshot) return false;
  return (
    includes(DATA_HEALTH_VALUES, snapshot.dataHealth) &&
    includes(PULLBACK_PHASE_VALUES, snapshot.pullbackPhase) &&
    includes(MOMENTUM_PHASE_VALUES, snapshot.momentumPhase) &&
    includes(DOMINANT_PHASE_VALUES, snapshot.dominantPhase) &&
    includes(CANDIDATE_STATUS_VALUES, snapshot.candidateStatus) &&
    (snapshot.selectedPath == null ||
      includes(SIGNAL_PATH_VALUES, snapshot.selectedPath)) &&
    snapshot.stateSchemaVersion === SIGNAL_STATE_SCHEMA_VERSION &&
    snapshot.featureSchemaVersion === SIGNAL_FEATURE_SCHEMA_VERSION &&
    typeof snapshot.policyVersion === 'string' &&
    snapshot.policyVersion.length > 0
  );
}

export function canApproveSnapshot(
  snapshot: SignalSnapshot | null | undefined,
  now = new Date()
): snapshot is SignalSnapshot & {
  candidateId: string;
  candidateFingerprint: string;
  pendingEntryIntentId: string;
} {
  if (!isKnownSignalSnapshot(snapshot)) return false;
  const expiresAt = snapshot.candidateExpiresAt
    ? Date.parse(snapshot.candidateExpiresAt)
    : Number.NaN;
  return (
    snapshot.candidateStatus === 'AWAITING_APPROVAL' &&
    Boolean(
      snapshot.candidateId &&
      snapshot.candidateFingerprint &&
      snapshot.pendingEntryIntentId
    ) &&
    snapshot.candidateStateVersion > 0 &&
    Number.isFinite(expiresAt) &&
    expiresAt > now.getTime()
  );
}

export function classifyFreshness(
  value: string | null | undefined,
  now: Date,
  kind: 'QUOTE' | 'HEARTBEAT',
  isCurrentTradingDay?: boolean
): Freshness {
  if (isCurrentTradingDay === false || !isTradingHours(now)) {
    return { ageSeconds: null, label: '休市·最近快照', level: 'CLOSED' };
  }
  if (!value) {
    return { ageSeconds: null, label: '等待数据', level: 'MISSING' };
  }
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) {
    return { ageSeconds: null, label: '时间异常', level: 'STALE' };
  }
  const ageSeconds = Math.max(0, (now.getTime() - timestamp) / 1000);
  const liveLimit = kind === 'QUOTE' ? 5 : 15;
  const delayedLimit = kind === 'QUOTE' ? 15 : 30;
  if (ageSeconds <= liveLimit) {
    return {
      ageSeconds,
      label: kind === 'QUOTE' ? '实时' : '心跳正常',
      level: 'LIVE',
    };
  }
  if (ageSeconds <= delayedLimit) {
    return { ageSeconds, label: '延迟', level: 'DELAYED' };
  }
  return { ageSeconds, label: '陈旧', level: 'STALE' };
}

function candidateSortRank(snapshot?: SignalSnapshot | null) {
  if (snapshot?.candidateStatus === 'AWAITING_APPROVAL') return 0;
  if (snapshot?.candidateStatus === 'LATCHED') return 1;
  if (
    snapshot?.opportunityScore != null &&
    snapshot.opportunityScore >= snapshot.previewThreshold
  ) {
    return 2;
  }
  if (snapshot?.dataHealth === 'READY') return 3;
  return 4;
}

export function buildAttentionRows<TQuote extends { time: string }>(
  holdings: readonly MonitorHolding[],
  sessions: readonly MonitorSession[],
  quotes: ReadonlyMap<string, TQuote>
): AttentionRow<TQuote>[] {
  const sessionsByCode = new Map(
    sessions
      .filter(session => session.stockCode)
      .map(session => [String(session.stockCode).toUpperCase(), session])
  );
  return holdings
    .map(holding => {
      const code = holding.stockCode.toUpperCase();
      const session = sessionsByCode.get(code) || holding.session;
      const snapshot = session?.signalSnapshot;
      const distanceToCandidate =
        snapshot?.opportunityScore == null
          ? null
          : Math.max(
              0,
              snapshot.candidateThreshold - snapshot.opportunityScore
            );
      return {
        attentionLevel: candidateSortRank(snapshot),
        distanceToCandidate,
        holding,
        quote: quotes.get(holding.stockCode) || quotes.get(code),
        session,
        snapshot,
      };
    })
    .sort(
      (left, right) =>
        left.attentionLevel - right.attentionLevel ||
        (left.distanceToCandidate ?? Number.POSITIVE_INFINITY) -
          (right.distanceToCandidate ?? Number.POSITIVE_INFINITY) ||
        left.holding.stockCode.localeCompare(right.holding.stockCode)
    );
}

import { describe, expect, it, vi } from 'vitest';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import {
  buildAttentionRows,
  canApproveSnapshot,
  classifyFreshness,
  createSignalSnapshotRefreshCoordinator,
  createTTradeClientTelemetryReporter,
  DOMINANT_PHASE_VALUES,
  isKnownSignalSnapshot,
  type MonitorHolding,
  type SignalSnapshot,
} from './monitoring';
import { sampleQuoteHistory } from './useLiveQuoteHistory';

function snapshot(overrides: Partial<SignalSnapshot> = {}): SignalSnapshot {
  return {
    instrumentCode: '600000.SH',
    tradeDate: '2026-08-13',
    evaluatedAt: '2026-08-13T10:00:00+08:00',
    sourceAt: '2026-08-13T10:00:00+08:00',
    sourceTimeMs: '1786586400000',
    tickOrdinal: '1',
    continuityGeneration: '2',
    dataAgeMs: 100,
    windowCoverageSeconds: 60,
    sampleCount: 30,
    dataHealth: 'READY',
    dataHealthReasons: [],
    pullbackPhase: 'REBOUND_CONFIRMING',
    momentumPhase: 'MOMENTUM_BUILDING',
    dominantPhase: 'PULLBACK_REBOUND_CONFIRMING',
    selectedPath: 'PULLBACK_REBOUND',
    pullbackScore: 74,
    momentumScore: 58,
    opportunityScore: 74,
    previewThreshold: 55,
    candidateThreshold: 72,
    revalidateThreshold: 60,
    rearmThreshold: 45,
    features: { sampleCount: 30 },
    pullback: {
      phase: 'REBOUND_CONFIRMING',
      score: 74,
      preview: true,
      candidateReady: true,
      hardGates: [],
      scoreContributions: [],
      blockers: [],
    },
    momentum: {
      phase: 'MOMENTUM_BUILDING',
      score: 58,
      preview: true,
      candidateReady: false,
      hardGates: [],
      scoreContributions: [],
      blockers: [],
    },
    hardGates: [
      { code: 'DATA_READY', label: '数据', passed: true, detail: '' },
    ],
    scoreContributions: [],
    topBlockers: [],
    candidateId: 'candidate-1',
    candidateFingerprint: 'fingerprint-1',
    candidateStatus: 'AWAITING_APPROVAL',
    candidateCreatedAt: '2026-08-13T10:00:00+08:00',
    candidateExpiresAt: '2026-08-13T10:00:30+08:00',
    pendingEntryIntentId: 'intent-1',
    signalVersion: 8,
    candidateStateVersion: 3,
    stateSchemaVersion: '3',
    featureSchemaVersion: '1',
    policyVersion: 't_trade_opportunity_v3.0.0',
    configVersion: 4,
    ...overrides,
  };
}

function holding(
  stockCode: string,
  signalSnapshot: SignalSnapshot | null
): MonitorHolding {
  return {
    stockCode,
    instrumentName: stockCode,
    volume: 1000,
    availableVolume: 1000,
    ignored: false,
    eligible: true,
    status: 'MONITORED',
    reason: '',
    session: {
      runId: `run-${stockCode}`,
      runStatus: 'RUNNING',
      status: 'OBSERVING',
      mode: 'paper',
      entryOrderStatus: '',
      exitOrderStatus: '',
      entryFilledVolume: 0,
      entryAvgPrice: 0,
      exitFilledVolume: 0,
      exitAvgPrice: 0,
      activeVolume: 0,
      lastNetProfitPct: 0,
      peakNetProfitPct: 0,
      profitArmed: false,
      lastExitReason: '',
      completedCycles: 0,
      canCancel: true,
      signalSnapshot,
    },
  };
}

function quote(stockCode: string, time: string, price = 10): LiveMarketQuote {
  return {
    stockCode,
    currentPrice: price,
    high: price,
    low: price,
    open: price,
    volume: 100,
    time,
  };
}

describe('T trade V3 monitoring helpers', () => {
  it('keeps null distinct from a real zero and fails closed on unknown enums', () => {
    expect(isKnownSignalSnapshot(snapshot({ opportunityScore: 0 }))).toBe(true);
    expect(
      isKnownSignalSnapshot(snapshot({ dataHealth: 'FUTURE_READY' }))
    ).toBe(false);
  });

  it('accepts every published dominant phase and rejects missing or unknown values', () => {
    for (const dominantPhase of DOMINANT_PHASE_VALUES) {
      expect(isKnownSignalSnapshot(snapshot({ dominantPhase }))).toBe(true);
    }
    expect(
      isKnownSignalSnapshot(snapshot({ dominantPhase: 'FUTURE_PHASE' }))
    ).toBe(false);
    expect(
      isKnownSignalSnapshot(snapshot({ dominantPhase: undefined }))
    ).toBe(false);
  });

  it('requires the exact V3 state and feature schema versions', () => {
    expect(isKnownSignalSnapshot(snapshot())).toBe(true);
    expect(
      isKnownSignalSnapshot(snapshot({ stateSchemaVersion: '2' }))
    ).toBe(false);
    expect(
      isKnownSignalSnapshot(snapshot({ featureSchemaVersion: 'features-v3' }))
    ).toBe(false);
    expect(
      isKnownSignalSnapshot(snapshot({ stateSchemaVersion: '' }))
    ).toBe(false);
    expect(
      isKnownSignalSnapshot(snapshot({ featureSchemaVersion: undefined }))
    ).toBe(false);
  });

  it('only enables approval for a fresh, versioned server candidate', () => {
    const now = new Date('2026-08-13T10:00:10+08:00');
    expect(canApproveSnapshot(snapshot(), now)).toBe(true);
    // Score, health and gates are server-owned eligibility. A valid pending
    // candidate remains confirmable here; the mutation revalidates it again.
    expect(
      canApproveSnapshot(
        snapshot({
          dataHealth: 'WARMING',
          opportunityScore: null,
          hardGates: [
            { code: 'DATA_READY', label: '数据', passed: false, detail: '' },
          ],
          topBlockers: [{ code: 'PAUSED', label: '暂停', detail: '服务端提示' }],
        }),
        now
      )
    ).toBe(true);
    expect(
      canApproveSnapshot(
        snapshot({ candidateExpiresAt: '2026-08-13T10:00:09+08:00' }),
        now
      )
    ).toBe(false);
    expect(
      canApproveSnapshot(
        snapshot({
          pendingEntryIntentId: null,
        }),
        now
      )
    ).toBe(false);
  });

  it('sorts awaiting, latched, preview, other READY, then non-READY', () => {
    const rows = [
      holding(
        '600005.SH',
        snapshot({
          dataHealth: 'WARMING',
          opportunityScore: null,
          candidateStatus: 'NONE',
        })
      ),
      holding(
        '600004.SH',
        snapshot({ opportunityScore: 20, candidateStatus: 'NONE' })
      ),
      holding(
        '600003.SH',
        snapshot({ opportunityScore: 60, candidateStatus: 'NONE' })
      ),
      holding(
        '600002.SH',
        snapshot({ candidateStatus: 'LATCHED', pendingEntryIntentId: null })
      ),
      holding('600001.SH', snapshot()),
    ];
    expect(
      buildAttentionRows(rows, [], new Map()).map(row => row.holding.stockCode)
    ).toEqual([
      '600001.SH',
      '600002.SH',
      '600003.SH',
      '600004.SH',
      '600005.SH',
    ]);
  });

  it('classifies UI transport freshness without turning it into signal health', () => {
    const trading = new Date('2026-08-13T10:00:20+08:00');
    expect(
      classifyFreshness('2026-08-13T10:00:16+08:00', trading, 'QUOTE').level
    ).toBe('LIVE');
    expect(
      classifyFreshness('2026-08-13T09:59:00+08:00', trading, 'HEARTBEAT').level
    ).toBe('STALE');
    expect(
      classifyFreshness(
        '2026-08-13T10:00:00+08:00',
        new Date('2026-08-13T12:00:00+08:00'),
        'QUOTE'
      ).level
    ).toBe('CLOSED');
  });

  it('samples only new quote updates and keeps a bounded two-minute window', () => {
    const now = new Date('2026-08-13T10:00:00+08:00').getTime();
    const quotes = new Map([
      ['600000.SH', quote('600000.SH', '2026-08-13T10:00:00+08:00')],
    ]);
    const first = sampleQuoteHistory(new Map(), quotes, now);
    expect(
      sampleQuoteHistory(first, quotes, now + 1000).get('600000.SH')
    ).toHaveLength(1);
  });

  it('fire-and-forgets telemetry with a fixed-event bounded throttle', async () => {
    let now = 1_000;
    const send = vi.fn(async () => undefined);
    const report = createTTradeClientTelemetryReporter(send, {
      now: () => now,
      throttleMs: 30_000,
    });

    expect(report('REFRESH_SUCCESS')).toBe(true);
    expect(report('REFRESH_SUCCESS')).toBe(false);
    expect(
      report('FREE_TEXT_ERROR' as never),
      'runtime callers cannot grow the label set'
    ).toBe(false);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(send).toHaveBeenCalledTimes(1);

    now += 29_999;
    expect(report('REFRESH_SUCCESS')).toBe(false);
    now += 1;
    expect(report('REFRESH_SUCCESS')).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('silently contains telemetry transport failures', async () => {
    const send = vi.fn(() => {
      throw new Error('telemetry unavailable');
    });
    const report = createTTradeClientTelemetryReporter(send);

    expect(() => report('REFRESH_FAILURE')).not.toThrow();
    await Promise.resolve();
    await Promise.resolve();
    expect(send).toHaveBeenCalledOnce();
  });

  it('never trusts an old reconnect epoch after disconnect and queues the new fetch', async () => {
    const coordinator = createSignalSnapshotRefreshCoordinator();
    const firstEpoch = coordinator.beginEpoch('account-1');
    let resolveFirst!: (value: boolean) => void;
    const firstRequest = new Promise<boolean>(resolve => {
      resolveFirst = resolve;
    });
    const firstRefresh = coordinator.refresh(
      firstEpoch,
      'account-1',
      () => firstRequest
    );

    coordinator.beginEpoch('account-1');
    const secondEpoch = coordinator.beginEpoch('account-1');
    let secondStarted = false;
    let resolveSecond!: (value: boolean) => void;
    const secondRequest = new Promise<boolean>(resolve => {
      resolveSecond = resolve;
    });
    const secondRefresh = coordinator.refresh(
      secondEpoch,
      'account-1',
      () => {
        secondStarted = true;
        return secondRequest;
      }
    );

    resolveFirst(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(await firstRefresh).toBe(false);
    expect(secondStarted).toBe(true);
    expect(coordinator.isTrusted('account-1')).toBe(false);

    resolveSecond(true);
    expect(await secondRefresh).toBe(true);
    expect(coordinator.isTrusted('account-1')).toBe(true);
  });

  it('rejects completion after a later account epoch even when the request succeeds', async () => {
    const coordinator = createSignalSnapshotRefreshCoordinator();
    const epoch = coordinator.beginEpoch('account-1');
    let resolveRequest!: (value: boolean) => void;
    const request = new Promise<boolean>(resolve => {
      resolveRequest = resolve;
    });
    const refresh = coordinator.refresh(epoch, 'account-1', () => request);

    coordinator.beginEpoch('account-2');
    resolveRequest(true);
    expect(await refresh).toBe(false);
    expect(coordinator.isTrusted('account-1')).toBe(false);
    expect(coordinator.isTrusted('account-2')).toBe(false);
  });

  it('keeps the current epoch untrusted when its network-only cycle fails', async () => {
    const coordinator = createSignalSnapshotRefreshCoordinator();
    const failedEpoch = coordinator.beginEpoch('account-1');

    expect(
      await coordinator.refresh(failedEpoch, 'account-1', async () => false)
    ).toBe(false);
    expect(coordinator.isTrusted('account-1')).toBe(false);

    const rejectedEpoch = coordinator.beginEpoch('account-1');
    expect(
      await coordinator.refresh(rejectedEpoch, 'account-1', async () => {
        throw new Error('network unavailable');
      })
    ).toBe(false);
    expect(coordinator.isTrusted('account-1')).toBe(false);
  });
});

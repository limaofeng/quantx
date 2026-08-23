import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { SignalSnapshot } from './monitoring';
import type { TTradeMonitorLike } from './TTradeLiveMonitor';
import {
  TTradeSignalsView,
  type CandidateTraceLike,
} from './TTradeSignalsView';

function snapshot(overrides: Partial<SignalSnapshot> = {}): SignalSnapshot {
  return {
    instrumentCode: '600000.SH',
    tradeDate: '2026-08-13',
    evaluatedAt: new Date().toISOString(),
    sourceAt: new Date().toISOString(),
    sourceTimeMs: '1',
    tickOrdinal: '1',
    continuityGeneration: '1',
    sampleCount: 20,
    dataHealth: 'READY',
    dataHealthReasons: [],
    pullbackPhase: 'REBOUND_CONFIRMING',
    momentumPhase: 'OBSERVING',
    dominantPhase: 'PULLBACK_REBOUND_CONFIRMING',
    selectedPath: 'PULLBACK_REBOUND',
    pullbackScore: 75,
    momentumScore: null,
    opportunityScore: 75,
    previewThreshold: 55,
    candidateThreshold: 72,
    revalidateThreshold: 60,
    rearmThreshold: 45,
    features: { sampleCount: 20, price: 10 },
    pullback: {
      phase: 'REBOUND_CONFIRMING',
      score: 75,
      preview: true,
      candidateReady: true,
      hardGates: [],
      scoreContributions: [],
      blockers: [],
    },
    momentum: {
      phase: 'OBSERVING',
      score: null,
      preview: false,
      candidateReady: false,
      hardGates: [],
      scoreContributions: [],
      blockers: [],
    },
    hardGates: [
      { code: 'DATA_READY', label: '数据就绪', passed: true, detail: '' },
    ],
    scoreContributions: [],
    topBlockers: [],
    candidateId: 'candidate-1',
    candidateFingerprint: 'fingerprint-1',
    candidateStatus: 'AWAITING_APPROVAL',
    candidateExpiresAt: new Date(Date.now() + 60_000).toISOString(),
    pendingEntryIntentId: 'intent-1',
    signalVersion: 1,
    candidateStateVersion: 1,
    stateSchemaVersion: '3',
    featureSchemaVersion: '1',
    policyVersion: 'policy-v3',
    configVersion: 1,
    ...overrides,
  };
}

function monitor(signalSnapshot: SignalSnapshot): TTradeMonitorLike {
  const session = {
    runId: 'run-1',
    stockCode: '600000.SH',
    runStatus: 'RUNNING',
    status: 'OBSERVING',
    mode: 'paper',
    pendingEntryIntentId: 'intent-1',
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
    plannedEntryAmount: 10_000,
    signalSnapshot,
  };
  return {
    enabled: true,
    mode: 'paper',
    holdingCount: 1,
    eligibleCount: 1,
    monitoredCount: 1,
    pendingSignalCount: 1,
    activeBatchCount: 0,
    drainingCount: 0,
    ignoredCount: 0,
    positionSnapshotComplete: true,
    rolloutStage: 'SHADOW',
    engineStatus: 'READY',
    agentStatus: 'READY',
    reconcileStatus: 'READY',
    killSwitch: false,
    canActivateLive: false,
    blockedReasons: [],
    sessions: [session],
    holdings: [
      {
        stockCode: '600000.SH',
        instrumentName: '测试股票',
        volume: 1000,
        availableVolume: 1000,
        ignored: false,
        eligible: true,
        status: 'MONITORED',
        reason: '',
        session,
      },
    ],
  };
}

function trace(
  overrides: Partial<CandidateTraceLike> = {}
): CandidateTraceLike {
  return {
    accountId: 'account-1',
    candidateId: 'candidate-1',
    strategyRunId: 'run-1',
    instrumentCode: '600000.SH',
    sourceEvaluationId: 'evaluation-1',
    integrityStatus: 'BROKEN',
    sourceIdentity: {
      sourceTimeMs: '1770000000000',
      tickOrdinal: '9',
      continuityGeneration: 'generation-1',
      tradeDate: '2026-08-23',
      candidateFingerprint: 'fingerprint-1',
      policyVersion: 'policy-v3',
      featureSchemaVersion: 'feature-v3',
      profileVersion: 'profile-v1',
    },
    missingReasons: [
      {
        code: 'TRADE_FACT_NOT_FOUND',
        stage: 'BROKER_TRADE',
        expected: false,
        detail: '委托已声明成交但成交事实缺失',
      },
    ],
    links: {
      evaluationIds: ['evaluation-1'],
      intentIds: ['intent-1'],
      clientOrderIds: ['client-1'],
      correlationIds: ['correlation-1'],
      brokerOrderIds: ['1001'],
      orderIds: ['1001'],
      tradeIds: [],
      batchIds: ['batch-1'],
      exitPlanIds: [],
      exitPlanEventIds: [],
    },
    events: [
      {
        stage: 'EVALUATION',
        eventType: 'CANDIDATE_CREATED',
        entityId: 'evaluation-1',
        occurredAt: '2026-08-23T01:30:00Z',
        status: 'AWAITING_APPROVAL',
        relatedIds: { candidate_id: ['candidate-1'] },
        details: { opportunity_score: 75 },
      },
    ],
    ...overrides,
  };
}

describe('TTradeSignalsView approval safety', () => {
  it('shows monitor loading before the first snapshot instead of an empty board', () => {
    render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted={false}
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations
        loadingMonitor
        monitor={undefined}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        quotes={new Map()}
      />
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '读取服务端信号快照'
    );
    expect(screen.queryByText('暂无可展示持仓')).not.toBeInTheDocument();
  });

  it('marks evaluation evidence as failed while retaining the last result', () => {
    render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted
        evaluationsError="连接超时"
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(snapshot())}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        quotes={new Map()}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'MATERIAL 评估证据读取失败'
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      '当前没有可展示的历史证据'
    );
  });

  it('surfaces a server monitor error without hiding the last signal snapshot', () => {
    render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(snapshot())}
        monitorError="projection unavailable"
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        quotes={new Map()}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('账户监控服务返回异常');
    expect(screen.getByRole('button', { name: '确认买入' })).toBeInTheDocument();
  });

  it('keeps the last snapshot visible but disables approval when refetch trust is lost', () => {
    render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted={false}
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(snapshot())}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        quotes={new Map()}
      />
    );
    expect(screen.getByText(/最后一个可信快照/)).toBeInTheDocument();
    expect(screen.getAllByText(/源时间/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '确认买入' })).toBeDisabled();
  });

  it('fails closed on an unknown server enum', () => {
    render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(snapshot({ candidateStatus: 'FUTURE_STATUS' }))}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        quotes={new Map()}
      />
    );
    expect(
      screen.queryByRole('button', { name: '确认买入' })
    ).not.toBeInTheDocument();
  });

  it('opens a durable candidate trace from MATERIAL evidence and distinguishes integrity', () => {
    const onRequestCandidateTrace = vi.fn();
    const signal = snapshot();
    const { rerender } = render(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        dataTrusted
        evaluations={[
          {
            id: 'evaluation-1',
            accountId: 'account-1',
            runId: 'run-1',
            stockCode: '600000.SH',
            eventKind: 'MATERIAL',
            eventType: 'CANDIDATE_CREATED',
            evaluatedAt: '2026-08-23T01:30:00Z',
            coalescedCount: 1,
            policyVersion: 'policy-v3',
            signalSnapshot: signal,
          },
        ]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(signal)}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        onRequestCandidateTrace={onRequestCandidateTrace}
        quotes={new Map()}
      />
    );

    fireEvent.click(
      screen.getByRole('button', { name: /追溯候选 candidate-1/ })
    );
    expect(onRequestCandidateTrace).toHaveBeenCalledWith({
      accountId: 'account-1',
      strategyRunId: 'run-1',
      candidateId: 'candidate-1',
    });

    rerender(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        candidateTrace={trace()}
        dataTrusted
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(signal)}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        onRequestCandidateTrace={onRequestCandidateTrace}
        quotes={new Map()}
        selectedTrace={{
          accountId: 'account-1',
          strategyRunId: 'run-1',
          candidateId: 'candidate-1',
        }}
      />
    );

    expect(screen.getByText('链路断裂')).toBeInTheDocument();
    expect(screen.getByText(/异常缺失 · 券商成交/)).toBeInTheDocument();
    expect(
      screen.getByText(/机会评估 · CANDIDATE_CREATED/)
    ).toBeInTheDocument();
    expect(screen.getByText(/policy policy-v3/)).toBeInTheDocument();
    expect(screen.getByText('intent-1')).toBeInTheDocument();
    expect(screen.getByText(/candidate_id · candidate-1/)).toBeInTheDocument();

    rerender(
      <TTradeSignalsView
        accountId="account-1"
        actionLoading={false}
        canApproveAccount
        candidateTrace={trace({ accountId: 'account-2' })}
        dataTrusted
        evaluations={[]}
        hasMoreEvaluations={false}
        loadingEvaluations={false}
        monitor={monitor(signal)}
        onApprove={vi.fn()}
        onLoadMoreEvaluations={vi.fn()}
        onReject={vi.fn()}
        onRequestCandidateTrace={onRequestCandidateTrace}
        quotes={new Map()}
        selectedTrace={{
          accountId: 'account-1',
          strategyRunId: 'run-1',
          candidateId: 'candidate-1',
        }}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('不一致');
    expect(screen.queryByText('intent-1')).not.toBeInTheDocument();
  });
});

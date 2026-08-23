import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { SignalSnapshot } from './monitoring';
import {
  TTradeSignalDiagnosticsPanel,
  type SignalDiagnosticsLike,
} from './TTradeSignalDiagnostics';

function snapshot(overrides: Partial<SignalSnapshot> = {}): SignalSnapshot {
  return {
    instrumentCode: '600000.SH',
    tradeDate: '2026-08-13',
    evaluatedAt: '2026-08-13T10:00:00+08:00',
    sourceAt: '2026-08-13T10:00:00+08:00',
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
    hardGates: [],
    scoreContributions: [],
    topBlockers: [],
    candidateId: null,
    candidateFingerprint: null,
    candidateStatus: 'NONE',
    candidateExpiresAt: null,
    pendingEntryIntentId: null,
    signalVersion: 1,
    candidateStateVersion: 1,
    stateSchemaVersion: '3',
    featureSchemaVersion: '1',
    policyVersion: 'policy-v3',
    configVersion: 1,
    profileVersion: 'profile-1',
    ...overrides,
  };
}

const diagnostics: SignalDiagnosticsLike = {
  available: true,
  accountId: 'account-1',
  startTime: '2026-08-13T09:30:00+08:00',
  endTime: '2026-08-13T15:00:00+08:00',
  mergedVersions: false,
  warnings: [],
  partitions: [
    {
      policyVersion: 'policy-v3',
      featureSchemaVersion: '1',
      profileVersion: 'profile-1',
      denominator: {
        code: 'READY_INSTRUMENT_SECONDS',
        label: 'READY 标的秒',
        readyInstrumentSeconds: 3600,
      },
      funnel: [
        {
          code: 'ELIGIBLE',
          label: 'eligible',
          unitCode: 'MATERIAL_EVENTS',
          denominatorCode: null,
          count: 100,
          conversionRate: null,
        },
      ],
      blockers: [
        {
          blocker: {
            code: 'SPREAD',
            label: '价差过宽',
            detail: '超过门限',
          },
          count: 3,
          rate: 0.03,
          denominatorCode: 'MATERIAL_EVENTS',
          denominatorValue: 100,
        },
      ],
      scoreDistribution: [
        {
          policyVersion: 'policy-v3',
          featureSchemaVersion: '1',
          profileVersion: 'profile-1',
          path: 'PULLBACK_REBOUND',
          lowerBound: 50,
          upperBound: 60,
          count: 4,
        },
      ],
      fsmDwell: [
        {
          branch: 'PULLBACK',
          phase: 'OBSERVING',
          durationSeconds: 120,
          transitionCount: 2,
        },
      ],
      fsmTransitions: [
        {
          branch: 'PULLBACK',
          fromPhase: 'OBSERVING',
          toPhase: 'PULLBACK_FORMING',
          count: 2,
        },
      ],
      candidateOutcomes: [
        { code: 'EXPIRED', label: '候选过期', count: 1 },
      ],
      postCandidatePerformance: {
        available: false,
        reasonCode: 'POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE',
        reason: '缺少权威成交费用账本和完整因果行情路径',
        sampleCount: 0,
        netMfePct: null,
        netMaePct: null,
        fixedWindowReturns: [],
        requiredDataCodes: ['AUTHORITATIVE_EXECUTION_FEE_LEDGER'],
      },
    },
  ],
  versionGroups: [
    {
      policyVersion: 'policy-v3',
      featureSchemaVersion: '1',
      profileVersion: 'profile-1',
      count: 10,
    },
  ],
};

describe('TTradeSignalDiagnosticsPanel', () => {
  it('shows an explicit loading state before the first diagnostics response', () => {
    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={undefined}
        evaluations={[]}
        loading
      />
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '正在读取近 20 日服务端诊断'
    );
    expect(screen.queryByText('诊断样本尚不可用')).not.toBeInTheDocument();
  });

  it('keeps a stale partition visible while a refresh fails', () => {
    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={diagnostics}
        error="连接超时"
        evaluations={[]}
        loading
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      '诊断刷新失败；以下仍显示上次成功读取的近 20 日分区'
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '正在刷新诊断，暂保留上次结果'
    );
    expect(screen.getByText('READY 标的秒')).toBeInTheDocument();
  });

  it('states the READY denominator and preserves unavailable conversion rates', () => {
    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={diagnostics}
        evaluations={[]}
        loading={false}
      />
    );
    expect(screen.getByText('READY 标的秒')).toBeInTheDocument();
    expect(screen.getByText('相对 起点 · 不可计算')).toBeInTheDocument();
    expect(screen.getByText('单位 MATERIAL_EVENTS')).toBeInTheDocument();
    expect(screen.getByText('不同规则版本默认不合并')).toBeInTheDocument();
    expect(screen.getByText(/分母 MATERIAL_EVENTS =/)).toBeInTheDocument();
    expect(
      screen.getByText('MFE / MAE / 固定窗口收益未计算')
    ).toBeInTheDocument();
    expect(
      screen.getByText('AUTHORITATIVE_EXECUTION_FEE_LEDGER')
    ).toBeInTheDocument();
    expect(screen.getByText(/OBSERVING → PULLBACK_FORMING/)).toBeInTheDocument();
  });

  it('renders fixed-window returns and keeps evaluation counts on their version partition', () => {
    const secondPartition = {
      ...diagnostics.partitions[0],
      policyVersion: 'policy-v4',
      profileVersion: 'profile-2',
      postCandidatePerformance: {
        ...diagnostics.partitions[0].postCandidatePerformance,
        available: true,
        sampleCount: 2,
        netMfePct: 1.2,
        netMaePct: -0.4,
        fixedWindowReturns: [
          {
            windowSeconds: 60,
            sampleCount: 2,
            averageNetReturnPct: 0.25,
          },
        ],
        requiredDataCodes: [],
      },
    };
    const firstAvailablePartition = {
      ...diagnostics.partitions[0],
      postCandidatePerformance: secondPartition.postCandidatePerformance,
    };
    const partitioned = {
      ...diagnostics,
      partitions: [firstAvailablePartition, secondPartition],
      versionGroups: [
        diagnostics.versionGroups[0],
        {
          policyVersion: 'policy-v4',
          featureSchemaVersion: '1',
          profileVersion: 'profile-2',
          count: 7,
        },
      ],
    };

    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={partitioned}
        evaluations={[]}
        loading={false}
      />
    );

    expect(screen.getByText('10 条')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /7 条评估/ })).toBeInTheDocument();
    expect(screen.getByText('固定窗口净收益')).toBeInTheDocument();
    expect(screen.getByText('0.25%')).toBeInTheDocument();
    expect(screen.getByText('2 样本')).toBeInTheDocument();
  });

  it('does not derive a partition count from the paged evaluation list', () => {
    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={{ ...diagnostics, versionGroups: [] }}
        evaluations={[
          {
            id: 'evaluation-1',
            accountId: 'account-1',
            runId: 'run-1',
            stockCode: '600000.SH',
            eventKind: 'MATERIAL',
            eventType: 'CANDIDATE_CREATED',
            evaluatedAt: diagnostics.startTime,
            coalescedCount: 1,
            policyVersion: 'policy-v3',
            signalSnapshot: null,
          },
        ]}
        loading={false}
      />
    );

    expect(screen.getByText('样本不可用')).toBeInTheDocument();
  });

  it('filters the timeline by all three version coordinates and labels each row', () => {
    const secondPartition = {
      ...diagnostics.partitions[0],
      policyVersion: 'policy-v4',
      profileVersion: 'profile-2',
    };
    const partitioned = {
      ...diagnostics,
      partitions: [diagnostics.partitions[0], secondPartition],
      versionGroups: [
        ...diagnostics.versionGroups,
        {
          policyVersion: 'policy-v4',
          featureSchemaVersion: '1',
          profileVersion: 'profile-2',
          count: 2,
        },
      ],
    };
    const evaluations = [
      {
        id: 'timeline-v3',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'FIRST_ONLY',
        evaluatedAt: diagnostics.startTime,
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: snapshot(),
      },
      {
        id: 'timeline-v4',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'SECOND_ONLY',
        evaluatedAt: diagnostics.endTime,
        coalescedCount: 1,
        policyVersion: 'policy-v4',
        signalSnapshot: snapshot({
          policyVersion: 'policy-v4',
          profileVersion: 'profile-2',
        }),
      },
      {
        id: 'timeline-missing-snapshot',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'MISSING_SNAPSHOT',
        evaluatedAt: diagnostics.endTime,
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: null,
      },
    ];

    render(
      <TTradeSignalDiagnosticsPanel
        diagnostics={partitioned}
        evaluations={evaluations}
        loading={false}
      />
    );

    expect(screen.getByText(/FIRST_ONLY/)).toBeInTheDocument();
    expect(screen.queryByText(/SECOND_ONLY/)).not.toBeInTheDocument();
    expect(screen.queryByText(/MISSING_SNAPSHOT/)).not.toBeInTheDocument();
    expect(screen.getByText(/policy policy-v3 · feature 1 · profile profile-1/)).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox', { name: '诊断版本分区' }), {
      target: { value: 'policy-v4:1:profile-2' },
    });
    expect(screen.getByText(/SECOND_ONLY/)).toBeInTheDocument();
    expect(screen.queryByText(/FIRST_ONLY/)).not.toBeInTheDocument();
    expect(screen.getByText(/policy policy-v4 · feature 1 · profile profile-2/)).toBeInTheDocument();
  });
});

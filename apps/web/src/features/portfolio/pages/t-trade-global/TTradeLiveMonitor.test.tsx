import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import type { SignalSnapshot } from './monitoring';
import {
  TTradeHealthConsole,
  TTradeLiveBoard,
  type SignalEvaluationLike,
  type TTradeMonitorLike,
} from './TTradeLiveMonitor';

vi.mock('./useMarketDataHealth', () => ({
  useMarketDataHealth: () => ({ status: 'ready' }),
}));

const signalSnapshot: SignalSnapshot = {
  instrumentCode: '600000.SH',
  tradeDate: '2026-08-13',
  evaluatedAt: '2026-08-13T10:00:00+08:00',
  sourceAt: '2026-08-13T10:00:00+08:00',
  sourceTimeMs: '1786586400000',
  tickOrdinal: '12',
  continuityGeneration: '3',
  dataAgeMs: 80,
  windowCoverageSeconds: 60,
  sampleCount: 30,
  dataHealth: 'READY',
  dataHealthReasons: [],
  pullbackPhase: 'REBOUND_CONFIRMING',
  momentumPhase: 'MOMENTUM_BUILDING',
  dominantPhase: 'PULLBACK_REBOUND_CONFIRMING',
  selectedPath: 'PULLBACK_REBOUND',
  pullbackScore: 74,
  momentumScore: null,
  opportunityScore: 74,
  previewThreshold: 55,
  candidateThreshold: 72,
  revalidateThreshold: 60,
  rearmThreshold: 45,
  features: {
    sampleCount: 30,
    price: 10,
    sessionVwap: 9.96,
    windowHigh: 10.1,
    windowLow: 9.9,
    pullbackPct: 0.6,
    reboundPct: 0,
    spreadTicks: 1,
  },
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
    score: null,
    preview: false,
    candidateReady: false,
    hardGates: [],
    scoreContributions: [],
    blockers: [],
  },
  hardGates: [
    {
      code: 'DATA_READY',
      label: '数据就绪',
      passed: true,
      observedValue: 1,
      requiredValue: 1,
      detail: '因果窗口完整',
    },
  ],
  scoreContributions: [
    {
      code: 'PULLBACK_DEPTH',
      label: '回撤深度',
      points: 20,
      maxPoints: 25,
      observedValue: 0.6,
      targetValue: 0.8,
      detail: '回撤结构已形成',
    },
  ],
  topBlockers: [],
  candidateId: 'candidate-1',
  candidateFingerprint: 'fingerprint-1',
  candidateStatus: 'AWAITING_APPROVAL',
  candidateExpiresAt: '2026-08-13T10:00:30+08:00',
  pendingEntryIntentId: 'intent-1',
  signalVersion: 8,
  candidateStateVersion: 3,
  stateSchemaVersion: '3',
  featureSchemaVersion: '1',
  policyVersion: 'policy-v3',
  configVersion: 4,
};

const monitor = {
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
  sessions: [],
  holdings: [
    {
      stockCode: '600000.SH',
      instrumentName: '测试股票',
      volume: 1000,
      availableVolume: 800,
      ignored: false,
      eligible: true,
      status: 'MONITORED',
      reason: '监控中',
      session: {
        runId: 'run-1',
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
    },
  ],
} satisfies TTradeMonitorLike;

const quotes = new Map<string, LiveMarketQuote>([
  [
    '600000.SH',
    {
      stockCode: '600000.SH',
      currentPrice: 10,
      changePercent: 1,
      high: 10.1,
      low: 9.9,
      open: 9.9,
      volume: 1000,
      time: '2026-08-13T10:00:00+08:00',
    },
  ],
]);

describe('TTradeLiveBoard V3 inspector', () => {
  it('separates an empty signal snapshot from subscription transport trust', () => {
    render(
      <TTradeHealthConsole
        accountId="account-1"
        actionLoading={false}
        monitor={{ ...monitor, sessions: [] }}
        onReconcile={vi.fn()}
        onRefresh={vi.fn()}
        onToggleMonitoring={vi.fn()}
        quoteConnected
        quotes={quotes}
        refreshing={false}
        snapshotTrusted={false}
        toggleDisabled={false}
        wsStatus="connected"
      />
    );

    expect(screen.getByText('信号快照 READY')).toBeInTheDocument();
    expect(screen.getByText('等待快照')).toBeInTheDocument();
    expect(screen.getByText('订阅 / 真源')).toBeInTheDocument();
    expect(screen.getByText('订阅已连接 · 查询待复核')).toBeInTheDocument();

    const eyebrow = screen.getByText('Stateful opportunity V3');
    expect(eyebrow).toHaveClass('text-blue-300');
    expect(eyebrow).not.toHaveClass('text-red-300');

    const refreshButton = screen.getByRole('button', {
      name: '刷新健康控制台',
    });
    expect(refreshButton).toHaveClass(
      'hover:text-blue-200',
      'focus-visible:ring-blue-400/70'
    );
    expect(refreshButton).not.toHaveClass(
      'hover:text-red-200',
      'focus-visible:ring-red-500/60'
    );
  });

  it('uses the semantic primary action for starting a stopped monitor', () => {
    render(
      <TTradeHealthConsole
        accountId="account-1"
        actionLoading={false}
        monitor={{ ...monitor, enabled: false }}
        onReconcile={vi.fn()}
        onRefresh={vi.fn()}
        onToggleMonitoring={vi.fn()}
        quoteConnected
        quotes={quotes}
        refreshing={false}
        snapshotTrusted={false}
        toggleDisabled={false}
        wsStatus="connected"
      />
    );

    const startButton = screen.getByRole('button', { name: '启动监控' });
    expect(startButton).toHaveClass(
      'bg-primary',
      'text-primary-foreground',
      'hover:bg-primary/90'
    );
    expect(startButton).not.toHaveClass(
      'bg-red-500',
      'text-white',
      'hover:bg-red-400'
    );
  });

  it('shows a loading state before the first monitor snapshot instead of an empty state', () => {
    render(<TTradeLiveBoard loading monitor={undefined} quotes={quotes} />);
    expect(screen.getByRole('status')).toHaveTextContent(
      '读取服务端信号快照'
    );
    expect(screen.queryByText('暂无可展示持仓')).not.toBeInTheDocument();
  });

  it('labels a refresh while retaining the previous monitor snapshot', () => {
    render(<TTradeLiveBoard loading monitor={monitor} quotes={quotes} />);
    expect(screen.getByRole('status')).toHaveTextContent(
      '正在刷新服务端快照'
    );
    expect(screen.getByRole('button', { name: '检查 测试股票' })).toBeInTheDocument();
  });

  it('renders server score, preserves a real zero, and returns row focus on Escape', async () => {
    const user = userEvent.setup();
    render(
      <TTradeLiveBoard
        historyByCode={new Map()}
        loading={false}
        monitor={monitor}
        quotes={quotes}
      />
    );
    const rowButton = screen.getByRole('button', { name: '检查 测试股票' });
    expect(within(rowButton).getByText(/源时间/)).toBeInTheDocument();

    await user.click(rowButton);

    const inspector = screen.getByRole('dialog');
    expect(
      within(inspector).getAllByText('等待人工确认').length
    ).toBeGreaterThan(0);
    expect(
      within(inspector).getAllByText('74.0 / 72.0').length
    ).toBeGreaterThan(0);
    expect(within(inspector).getByText('0.00%')).toBeInTheDocument();
    expect(
      within(inspector).getByText('规则机会分 / 候选阈值，不是概率')
    ).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    );
    expect(rowButton).toHaveFocus();
  });

  it('shows null as unavailable instead of zero', async () => {
    const user = userEvent.setup();
    const unavailable = {
      ...monitor,
      holdings: [
        {
          ...monitor.holdings[0],
          session: {
            ...monitor.holdings[0].session,
            signalSnapshot: {
              ...signalSnapshot,
              opportunityScore: null,
              features: { ...signalSnapshot.features, reboundPct: null },
            },
          },
        },
      ],
    } satisfies TTradeMonitorLike;
    render(
      <TTradeLiveBoard loading={false} monitor={unavailable} quotes={quotes} />
    );
    await user.click(screen.getByRole('button', { name: '检查 测试股票' }));
    expect(
      within(screen.getByRole('dialog')).getAllByText('不可计算').length
    ).toBeGreaterThan(0);
  });

  it('draws all four server thresholds and breaks score lines across continuity generations', async () => {
    const user = userEvent.setup();
    const currentSnapshot = {
      ...signalSnapshot,
      sourceAt: '2026-08-13T10:00:04+08:00',
      sourceTimeMs: '1786586404000',
      tickOrdinal: '2',
      continuityGeneration: '4',
      opportunityScore: 74,
      previewThreshold: 58,
      candidateThreshold: 73,
      revalidateThreshold: 61,
      rearmThreshold: 46,
    } satisfies SignalSnapshot;
    const evaluations: SignalEvaluationLike[] = [
      {
        id: 'evaluation-1',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'TICK',
        evaluatedAt: '2026-08-13T10:00:01+08:00',
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: {
          ...signalSnapshot,
          sourceAt: '2026-08-13T10:00:01+08:00',
          sourceTimeMs: '1786586401000',
          tickOrdinal: '10',
          opportunityScore: 60,
          previewThreshold: 55,
        },
      },
      {
        id: 'evaluation-2',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'TICK',
        evaluatedAt: '2026-08-13T10:00:02+08:00',
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: {
          ...signalSnapshot,
          sourceAt: '2026-08-13T10:00:02+08:00',
          sourceTimeMs: '1786586402000',
          tickOrdinal: '11',
          opportunityScore: 64,
          previewThreshold: 56,
        },
      },
      {
        id: 'evaluation-3',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'STREAM_RESET',
        evaluatedAt: '2026-08-13T10:00:03+08:00',
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: {
          ...currentSnapshot,
          sourceAt: '2026-08-13T10:00:03+08:00',
          sourceTimeMs: '1786586403000',
          tickOrdinal: '1',
          opportunityScore: 70,
          previewThreshold: 57,
        },
      },
    ];
    const generationFourMonitor = {
      ...monitor,
      holdings: [
        {
          ...monitor.holdings[0],
          session: {
            ...monitor.holdings[0].session,
            signalSnapshot: currentSnapshot,
          },
        },
      ],
    } satisfies TTradeMonitorLike;

    render(
      <TTradeLiveBoard
        evaluations={evaluations}
        loading={false}
        monitor={generationFourMonitor}
        quotes={quotes}
      />
    );
    await user.click(screen.getByRole('button', { name: '检查 测试股票' }));

    const chart = within(screen.getByRole('dialog')).getByRole('img', {
      name: /四条服务端阈值趋势/,
    });
    const thresholdSeries = ['preview', 'candidate', 'revalidate', 'rearm'];
    for (const series of thresholdSeries) {
      expect(
        chart.querySelectorAll(`[data-series="${series}"]`).length
      ).toBeGreaterThan(0);
    }
    const scoreSegments = Array.from(
      chart.querySelectorAll<SVGPolylineElement>('[data-series="score"]')
    );
    expect(scoreSegments.map(item => item.dataset.generation)).toEqual([
      '3',
      '4',
    ]);
    expect(scoreSegments.map(item => item.dataset.pointCount)).toEqual([
      '2',
      '2',
    ]);
    const previewSegments = Array.from(
      chart.querySelectorAll<SVGPolylineElement>('[data-series="preview"]')
    );
    expect(previewSegments.map(item => item.dataset.values)).toEqual([
      '55,56',
      '57,58',
    ]);
  });

  it('breaks a threshold series at missing values and labels a missing current value unavailable', async () => {
    const user = userEvent.setup();
    const unavailableThreshold = {
      ...signalSnapshot,
      sourceAt: '2026-08-13T10:00:03+08:00',
      sourceTimeMs: '1786586403000',
      tickOrdinal: '14',
      previewThreshold: Number.NaN,
    } satisfies SignalSnapshot;
    const evaluations: SignalEvaluationLike[] = [
      {
        id: 'evaluation-before-gap',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'TICK',
        evaluatedAt: '2026-08-13T10:00:01+08:00',
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: {
          ...signalSnapshot,
          sourceTimeMs: '1786586401000',
          tickOrdinal: '12',
          previewThreshold: 55,
        },
      },
      {
        id: 'evaluation-gap',
        accountId: 'account-1',
        runId: 'run-1',
        stockCode: '600000.SH',
        eventKind: 'MATERIAL',
        eventType: 'TICK',
        evaluatedAt: '2026-08-13T10:00:02+08:00',
        coalescedCount: 1,
        policyVersion: 'policy-v3',
        signalSnapshot: {
          ...signalSnapshot,
          sourceTimeMs: '1786586402000',
          tickOrdinal: '13',
          previewThreshold: Number.NaN,
        },
      },
    ];
    const unavailableMonitor = {
      ...monitor,
      holdings: [
        {
          ...monitor.holdings[0],
          session: {
            ...monitor.holdings[0].session,
            signalSnapshot: unavailableThreshold,
          },
        },
      ],
    } satisfies TTradeMonitorLike;
    render(
      <TTradeLiveBoard
        evaluations={evaluations}
        loading={false}
        monitor={unavailableMonitor}
        quotes={quotes}
      />
    );
    await user.click(screen.getByRole('button', { name: '检查 测试股票' }));

    const inspector = within(screen.getByRole('dialog'));
    const chart = inspector.getByRole('img', {
      name: /四条服务端阈值趋势/,
    });
    const previewSegments = chart.querySelectorAll('[data-series="preview"]');
    expect(previewSegments).toHaveLength(1);
    expect(previewSegments[0]).toHaveAttribute('data-point-count', '1');
    expect(inspector.getAllByText('不可用').length).toBeGreaterThan(0);
  });
});

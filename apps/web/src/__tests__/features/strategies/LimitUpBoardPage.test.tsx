import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import LimitUpBoardPage from '@/features/strategies/pages/LimitUpBoardPage';

const mocks = vi.hoisted(() => ({
  approve: vi.fn(async () => ({ message: '已确认' })),
  arm: vi.fn(async () => ({ message: '已布防' })),
  disarm: vi.fn(async () => ({ message: '已取消' })),
  reconcile: vi.fn(async () => ({ message: '已同步' })),
  refreshRadar: vi.fn(),
  reject: vi.fn(async () => ({ message: '已忽略' })),
  save: vi.fn(async () => ({ message: '已保存' })),
  setLocation: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('wouter', () => ({
  useLocation: () => ['/limit-up-board', mocks.setLocation],
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

vi.mock(
  '@/features/strategies/components/LimitUpRadarMiniChart',
  () => ({ LimitUpRadarMiniChart: () => <div data-testid="radar-mini-chart" /> })
);

vi.mock('@/features/dashboard/hooks/useDashboard', () => ({
  useCurrentAccount: () => ({
    data: {
      currentAccount: {
        accountName: '模拟账户',
        accountType: 'STOCK',
        id: 'account-1',
      },
    },
    error: null,
    loading: false,
  }),
}));

vi.mock('@/features/strategies/hooks/useLimitUpRadar', () => ({
  useLimitUpRadar: () => ({
    candidates: [
      {
        amount: 528_000_000,
        amountPaceRatio: 2.4,
        ask1Price: 19.7,
        ask1Volume: 30_000,
        bid1Price: 19.69,
        bid1Volume: 120_000,
        blockedReasons: [],
        boardSegment: 'GROWTH',
        breakCount: 0,
        canCreateInstance: true,
        changePct: 8.8,
        code: '300001.SZ',
        currentPrice: 19.7,
        depthImbalance5: 0.45,
        distanceToLimitPct: 0.15,
        distanceToLimitTicks: 1,
        events: [],
        exitPolicyVersion: 'first-board-exit-v2-shadow-1',
        expectedNetReturnPct: 1.25,
        existingInstanceId: null,
        firstBoardCloseProbability: 0.72,
        industry: '软件服务',
        intradayTurnoverRatePct: 7.1,
        isStale: false,
        last5mVolumeRatio: 3.2,
        limitUpPrice: 19.71,
        name: '特锐德',
        nextDayLimitSealProbability: 0.28,
        nextDayLimitTouchProbability: 0.43,
        normalizedLimitProgress: 0.88,
        oneWordLimitUp: false,
        priceChange5mPct: 2.1,
        promotionEligible: true,
        promotionFactors: [],
        promotionModelVersion: 'first-board-promotion-v2-shadow-1',
        promotionObserved: true,
        promotionScore: 76,
        promotionSnapshotVersion: 'snapshot-1',
        radarScore: 88,
        researchArtifact: null,
        scoreBreakdown: [],
        scoreVersion: 'limit-up-radar-v1',
        stage: 'NEAR_LIMIT',
        stageLabel: '临板',
        highPositionType: 'BASE_BREAKOUT',
        updatedAt: new Date().toISOString(),
        volumePaceRatio: 2.1,
        cvar95LossPct: 5.5,
      },
    ],
    error: null,
    fetching: false,
    industries: [],
    industry: 'ALL',
    isScannerRunning: true,
    refresh: mocks.refreshRadar,
    search: '',
    setIndustry: vi.fn(),
    setSearch: vi.fn(),
    setStage: vi.fn(),
    stage: 'ALL',
    summary: {
      brokenCount: 0,
      candidateCount: 1,
      discoveredCount: 1,
      eligibleCount: 1,
      excludedCount: 80,
      nearLimitCount: 1,
      scannedCount: 5200,
      sealedCount: 0,
      staleCount: 0,
    },
    total: 1,
    updatedAt: new Date().toISOString(),
    warnings: [],
  }),
}));

vi.mock('@/features/strategies/hooks/useLimitUpBoardAssistant', () => ({
  useLimitUpBoardAssistant: () => ({
    approve: mocks.approve,
    arm: mocks.arm,
    assistant: {
      activeExitPlanCount: 1,
      armedCandidates: [],
      autoSignalMinScore: 70,
      canApprove: true,
      enabled: true,
      engineStatus: 'ONLINE',
      lastError: null,
      monitoredCount: 1,
      pendingSignalCount: 1,
      promotionModelMode: 'SHADOW',
    },
    currentSettings: {
      accountId: 'account-1',
      autoExitAcknowledged: false,
      autoSignalMinScore: 70,
      enabled: true,
      maxSinglePositionPct: 0.05,
      maxDailyExposurePct: 0.06,
      maxOpenPositions: 2,
      maxRankedCandidates: 5,
      mode: 'paper',
      plannedTailLossPct: 0.0015,
      promotionModelMode: 'SHADOW',
    },
    disarm: mocks.disarm,
    error: null,
    exitPlans: [
      {
        autoExitAuthorized: true,
        entryAvgPrice: 10,
        entryTradeDate: '2026-08-13',
        holdingTradingDays: 2,
        id: 'plan-1',
        instrumentCode: '000001.SZ',
        lastNetProfitPct: 1.2,
        lastPrice: 10.12,
        pendingOrderId: null,
        remainingVolume: 1000,
        ruleTypes: [
          'LIMIT_UP_TOUCH',
          'LIMIT_UP_BREAK',
          'TRAILING_PRICE_DRAWDOWN',
          'MAX_HOLDING_DAYS',
        ],
        status: 'ACTIVE',
      },
    ],
    fetching: false,
    pendingIntents: [
      {
        approvalExpiresAt: new Date(Date.now() + 15_000).toISOString(),
        distanceToLimitTicks: 1,
        id: 'intent-1',
        instrumentCode: '600000.SH',
        limitUpPrice: 19.71,
        signalPrice: 19.7,
        targetAmount: 10_000,
        targetPositionPct: 0.02,
      },
    ],
    reconcile: mocks.reconcile,
    refresh: vi.fn(),
    reject: mocks.reject,
    runId: 'run-1',
    save: mocks.save,
  }),
}));

describe('LimitUpBoardPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('puts market candidates first and removes the per-symbol instance flow', () => {
    render(<LimitUpBoardPage />);

    expect(screen.getByTestId('limit-up-board-page')).toBeVisible();
    expect(screen.getByText('首板晋级候选')).toBeVisible();
    expect(screen.getByText('特锐德')).toBeVisible();
    expect(screen.getByText('待确认信号')).toBeVisible();
    expect(screen.getByText('T+1 自适应退出')).toBeVisible();
    expect(screen.queryByText('全市场打板雷达')).not.toBeInTheDocument();
    expect(screen.queryByText('手动新建实例')).not.toBeInTheDocument();
    expect(screen.queryByText('实例管理')).not.toBeInTheDocument();
  });

  it('confirms a signal without exposing editable order fields', async () => {
    const user = userEvent.setup();
    render(<LimitUpBoardPage />);

    expect(screen.queryByLabelText('临时价格')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认买入' }));

    expect(mocks.approve).toHaveBeenCalledWith('intent-1');
  });

  it('marks an automatic candidate as preferred without bypassing rules', async () => {
    const user = userEvent.setup();
    render(<LimitUpBoardPage />);

    await user.click(screen.getByRole('button', { name: '优先关注' }));

    expect(mocks.arm).toHaveBeenCalledWith('300001.SZ');
  });
});

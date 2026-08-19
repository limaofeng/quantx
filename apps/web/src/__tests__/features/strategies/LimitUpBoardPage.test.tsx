import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import LimitUpBoardPage from '@/features/strategies/pages/LimitUpBoardPage';

const mocks = vi.hoisted(() => {
  const approve = vi.fn(async () => ({ message: '已确认' }));
  const arm = vi.fn(async () => ({ message: '已布防' }));
  const disarm = vi.fn(async () => ({ message: '已取消' }));
  const reconcile = vi.fn(async () => ({ message: '已同步' }));
  const refreshRadar = vi.fn();
  const reject = vi.fn(async () => ({ message: '已忽略' }));
  const save = vi.fn(async () => ({ message: '已保存' }));
  const firstIntent = {
    approvalExpiresAt: '2099-08-19T10:00:15+08:00',
    distanceToLimitTicks: 1,
    id: 'intent-1',
    instrumentCode: '600000.SH',
    limitUpPrice: 19.71,
    signalPrice: 19.7,
    targetAmount: 10_000,
    targetPositionPct: 0.02,
  };
  const assistantState = {
    approve,
    arm,
    assistant: {
      activeExitPlanCount: 1,
      armedCandidates: [],
      autoSignalMinScore: 70,
      blockedReasons: [],
      canApprove: true,
      enabled: true,
      engineStatus: 'ONLINE',
      killSwitch: false,
      lastError: null,
      monitoredCount: 1,
      pendingSignalCount: 1,
      projectionGeneratedAt: '2026-08-19T09:59:55+08:00',
      projectionVersion: '1',
      promotionModelMode: 'SHADOW',
      reconcileStatus: 'READY',
      runStatus: 'RUNNING',
    },
    currentSettings: {
      accountId: 'account-1',
      autoExitAcknowledged: false,
      autoSignalMinScore: 70,
      enabled: true,
      maxDailyExposurePct: 0.06,
      maxOpenPositions: 2,
      maxRankedCandidates: 5,
      maxSinglePositionPct: 0.05,
      mode: 'paper',
      plannedTailLossPct: 0.0015,
      promotionModelMode: 'SHADOW',
    },
    disarm,
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
    pendingIntents: [{ ...firstIntent }],
    reconcile,
    refresh: vi.fn(),
    reject,
    runId: 'run-1',
    save,
  };

  return {
    approve,
    arm,
    assistantState,
    disarm,
    firstIntent,
    reconcile,
    refreshRadar,
    reject,
    save,
    setLocation: vi.fn(),
    toast: vi.fn(),
  };
});

vi.mock('wouter', () => ({
  useLocation: () => ['/limit-up-board', mocks.setLocation],
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

vi.mock('@/features/strategies/components/LimitUpRadarMiniChart', () => ({
  LimitUpRadarMiniChart: () => <div data-testid="radar-mini-chart" />,
}));

vi.mock('@/features/dashboard/hooks/useAMarketSession', () => ({
  useAMarketSession: () => ({
    calendarError: null,
    calendarLoading: false,
    detail: '午间前持续交易',
    isOpen: true,
    isTradingDay: true,
    label: '交易中',
    now: new Date('2026-08-19T10:00:00+08:00'),
    phase: 'morning',
    targetTradingDate: '2026-08-19',
    tradingDays: ['2026-08-19'],
  }),
}));

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
        candidatePreference: 'AUTO',
        changePct: 8.8,
        code: '300001.SZ',
        currentPrice: 19.7,
        cvar95LossPct: 5.5,
        depthImbalance5: 0.45,
        distanceToLimitPct: 0.15,
        distanceToLimitTicks: 1,
        events: [],
        existingInstanceId: null,
        exitPolicyVersion: 'first-board-exit-v2-shadow-1',
        expectedNetReturnPct: 1.25,
        firstBoardCloseProbability: 0.72,
        highPositionType: 'BASE_BREAKOUT',
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
        updatedAt: '2026-08-19T10:00:00+08:00',
        volumePaceRatio: 2.1,
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
    updatedAt: '2026-08-19T10:00:00+08:00',
    warnings: [],
  }),
}));

vi.mock('@/features/strategies/hooks/useLimitUpBoardAssistant', () => ({
  useLimitUpBoardAssistant: () => mocks.assistantState,
}));

describe('LimitUpBoardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.assistantState.pendingIntents = [{ ...mocks.firstIntent }];
  });

  afterEach(() => vi.unstubAllGlobals());

  it('uses the full workbench width and exposes three accessible views', () => {
    render(<LimitUpBoardPage />);

    const root = screen.getByTestId('limit-up-board-page');
    expect(root).toHaveClass('w-full');
    expect(root).not.toHaveClass('max-w-[1540px]');

    const radarTab = screen.getByRole('tab', { name: /候选雷达/ });
    const signalsTab = screen.getByRole('tab', { name: /待确认信号/ });
    const positionsTab = screen.getByRole('tab', { name: /T\+1 持仓/ });

    expect(screen.getAllByRole('tab')).toHaveLength(3);
    expect(radarTab).toHaveAttribute('aria-selected', 'true');
    expect(signalsTab).toHaveAttribute(
      'aria-controls',
      'limit-up-workbench-view'
    );
    expect(positionsTab).toHaveAttribute(
      'aria-controls',
      'limit-up-workbench-view'
    );
    expect(screen.getByRole('tabpanel', { name: '候选雷达' })).toBeVisible();
  });

  it('docks health only when the measured workspace can keep the radar wide', () => {
    type ObserverCallback = (
      entries: ResizeObserverEntry[],
      observer: ResizeObserver
    ) => void;
    const observations = new Map<
      Element,
      { callback: ObserverCallback; observer: ResizeObserver }
    >();
    class ResizeObserverMock implements ResizeObserver {
      constructor(private readonly callback: ObserverCallback) {}

      disconnect() {}

      observe = (target: Element) => {
        observations.set(target, { callback: this.callback, observer: this });
      };

      unobserve() {}
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    render(<LimitUpBoardPage />);

    const root = screen.getByTestId('limit-up-board-page');
    const inlineHealth = screen.getByTestId('limit-up-inline-health');
    const healthButton = screen.getByRole('button', {
      name: '打开首板健康控制台',
    });
    const observation = observations.get(root);
    expect(observation).toBeDefined();
    if (!observation) throw new Error('page resize observer was not attached');
    const resizeEntry = (width: number): ResizeObserverEntry => ({
      borderBoxSize: [],
      contentBoxSize: [],
      contentRect: { width } as DOMRectReadOnly,
      devicePixelContentBoxSize: [],
      target: root,
    });

    act(() => {
      observation.callback([resizeEntry(1500)], observation.observer);
    });
    expect(inlineHealth).toHaveClass('flex');
    expect(healthButton).toHaveClass('hidden');

    act(() => {
      observation.callback([resizeEntry(1200)], observation.observer);
    });
    expect(inlineHealth).toHaveClass('hidden');
    expect(healthButton).not.toHaveClass('hidden');
  });

  it('opens the health console with first-board business checks', async () => {
    const user = userEvent.setup();
    render(<LimitUpBoardPage />);

    await user.click(
      screen.getByRole('button', { name: '打开首板健康控制台' })
    );

    const consoleDialog = await screen.findByRole('dialog', {
      name: '首板健康控制台',
    });
    const healthConsole = within(consoleDialog).getByTestId(
      'limit-up-health-console'
    );

    expect(within(healthConsole).getByText('业务链检查')).toBeVisible();
    for (const label of [
      '候选雷达',
      '晋级助手',
      '业务投影',
      '确认门禁',
      '监控负载',
      '退出托管',
    ]) {
      expect(within(healthConsole).getByText(label)).toBeVisible();
    }
  });

  it('opens the selected candidate in the right-side inspector', async () => {
    const user = userEvent.setup();
    render(<LimitUpBoardPage />);

    const candidateRow = screen.getByRole('row', {
      name: '查看 特锐德 300001.SZ',
    });
    await user.click(candidateRow);

    const inspectorDialog = await screen.findByRole('dialog', {
      name: '特锐德候选详情',
    });
    expect(within(inspectorDialog).getByText('生命周期走势')).toBeVisible();
    expect(
      within(inspectorDialog).getByText(/300001\.SZ · 首板晋级候选检查器/)
    ).toBeVisible();
    expect(
      within(inspectorDialog).getByTestId('radar-mini-chart')
    ).toBeVisible();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(candidateRow).toHaveFocus());
  });

  it('switches to pending signals and confirms without editable order fields', async () => {
    const user = userEvent.setup();
    render(<LimitUpBoardPage />);

    const signalsTab = screen.getByRole('tab', { name: /待确认信号/ });
    await user.click(signalsTab);

    expect(signalsTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: '待确认信号' })).toBeVisible();
    expect(screen.queryByLabelText('临时价格')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认买入' }));

    expect(mocks.approve).toHaveBeenCalledWith('intent-1');
  });

  it('auto-opens pending signals once when a new intent arrives after hydration', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<LimitUpBoardPage />);
    const radarTab = screen.getByRole('tab', { name: /候选雷达/ });

    expect(radarTab).toHaveAttribute('aria-selected', 'true');

    mocks.assistantState.pendingIntents = [
      { ...mocks.firstIntent },
      {
        ...mocks.firstIntent,
        id: 'intent-2',
        instrumentCode: '600001.SH',
      },
    ];
    rerender(<LimitUpBoardPage />);

    const signalsTab = screen.getByRole('tab', { name: /待确认信号/ });
    await waitFor(() =>
      expect(signalsTab).toHaveAttribute('aria-selected', 'true')
    );
    expect(screen.getByText('600001.SH')).toBeVisible();

    await user.click(radarTab);
    mocks.assistantState.pendingIntents = [
      ...mocks.assistantState.pendingIntents,
    ];
    rerender(<LimitUpBoardPage />);

    expect(radarTab).toHaveAttribute('aria-selected', 'true');
  });
});

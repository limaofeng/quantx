import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  StudioWorkspaceContext,
  type StudioWorkspaceContextValue,
} from '@/components/studio-workspace';
import LimitUpBoardPage from '@/features/strategies/pages/LimitUpBoardPage';

const mocks = vi.hoisted(() => {
  const approve = vi.fn(async () => ({ message: '已确认' }));
  const arm = vi.fn(async () => ({ message: '已布防' }));
  const disarm = vi.fn(async () => ({ message: '已取消' }));
  const reconcile = vi.fn(async () => ({ message: '已同步' }));
  const refreshRadar = vi.fn();
  const reject = vi.fn(async () => ({ message: '已忽略' }));
  const save = vi.fn(async () => ({ message: '已保存' }));
  const clearWorkspaceSidebar = vi.fn();
  const setWorkspaceSidebar = vi.fn();
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
    clearWorkspaceSidebar,
    disarm,
    firstIntent,
    reconcile,
    refreshRadar,
    reject,
    save,
    setLocation: vi.fn(),
    setWorkspaceSidebar,
    toast: vi.fn(),
  };
});

const workspaceContext: StudioWorkspaceContextValue = {
  activeTabId: 'page:/limit-up-board',
  clearWorkspaceSidebar: mocks.clearWorkspaceSidebar,
  isWorkspaceHosted: true,
  openStudioTab: vi.fn(),
  setWorkspaceSidebar: mocks.setWorkspaceSidebar,
  updateActiveTab: vi.fn(),
};

function HostedLimitUpBoardPage() {
  return (
    <StudioWorkspaceContext.Provider value={workspaceContext}>
      <LimitUpBoardPage />
    </StudioWorkspaceContext.Provider>
  );
}

vi.mock('wouter', () => ({
  useLocation: () => ['/limit-up-board', mocks.setLocation],
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

vi.mock('@/features/strategies/components/LimitUpRadarMiniChart', () => ({
  LimitUpRadarMiniChart: () => <div data-testid="radar-mini-chart" />,
}));

vi.mock(
  '@/features/strategies/components/limit-up-board-replay/LimitUpBoardReplayPanel',
  () => ({
    LimitUpBoardReplayPanel: ({ accountId }: { accountId?: string }) => (
      <div data-testid="board-replay-panel">历史回放 · {accountId}</div>
    ),
  })
);

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

  it('uses the full workbench width and exposes four accessible views', () => {
    render(<HostedLimitUpBoardPage />);

    const root = screen.getByTestId('limit-up-board-page');
    expect(root).toHaveClass('w-full');
    expect(root).not.toHaveClass('max-w-[1540px]');

    const navigation = screen.getByRole('tablist', {
      name: '首板工作区',
    });
    const radarTab = within(navigation).getByRole('tab', {
      name: /候选雷达/,
    });
    const signalsTab = within(navigation).getByRole('tab', {
      name: /待确认信号/,
    });
    const positionsTab = within(navigation).getByRole('tab', {
      name: /T\+1 持仓/,
    });
    const replayTab = within(navigation).getByRole('tab', {
      name: /历史回放/,
    });

    expect(within(navigation).getAllByRole('tab')).toHaveLength(4);
    expect(radarTab).toHaveAttribute('aria-selected', 'true');
    expect(signalsTab).toHaveAttribute(
      'aria-controls',
      'limit-up-workbench-view'
    );
    expect(positionsTab).toHaveAttribute(
      'aria-controls',
      'limit-up-workbench-view'
    );
    expect(replayTab).toHaveAttribute(
      'aria-controls',
      'limit-up-workbench-view'
    );
    expect(screen.getByRole('tabpanel', { name: '候选雷达' })).toBeVisible();
  });

  it('opens the account-level historical replay as an independent view', async () => {
    const user = userEvent.setup();
    render(<HostedLimitUpBoardPage />);

    await user.click(screen.getByRole('tab', { name: /历史回放/ }));

    expect(screen.getByRole('tabpanel', { name: '历史回放' })).toBeVisible();
    expect(await screen.findByTestId('board-replay-panel')).toHaveTextContent(
      '历史回放 · account-1'
    );
  });

  it('registers the same resizable workspace sidebar pattern as the T assistant', async () => {
    render(<HostedLimitUpBoardPage />);

    await waitFor(() => expect(mocks.setWorkspaceSidebar).toHaveBeenCalled());
    const sidebar = mocks.setWorkspaceSidebar.mock.lastCall?.[0];

    expect(sidebar).toMatchObject({
      showSidebar: true,
      sizing: {
        defaultWidth: 312,
        maxWidth: 420,
        minWidth: 260,
        storageScope: 'limit-up-board-studio',
      },
      themeName: 'red',
      title: '打板助手',
    });
    expect(sidebar?.content).toBeTruthy();
  });

  it('registers first-board business checks in the health console', async () => {
    const user = userEvent.setup();
    render(<HostedLimitUpBoardPage />);

    await waitFor(() => expect(mocks.setWorkspaceSidebar).toHaveBeenCalled());
    const sidebar = mocks.setWorkspaceSidebar.mock.lastCall?.[0];
    if (!sidebar?.content) throw new Error('health sidebar was not registered');
    const sidebarView = render(sidebar.content);
    const healthConsole = within(sidebarView.container).getByTestId(
      'limit-up-health-console'
    );

    expect(
      within(healthConsole).getByRole('heading', {
        level: 1,
        name: '健康控制台',
      })
    ).toBeVisible();
    expect(
      within(healthConsole).getByRole('group', { name: '首板运行摘要' })
    ).toBeVisible();
    const businessChecks = within(healthConsole).getByRole('list', {
      name: '首板业务链检查项',
    });
    expect(businessChecks).toBeVisible();
    expect(within(healthConsole).getByText('业务链检查')).toBeVisible();
    for (const label of [
      '候选雷达',
      '晋级助手',
      '业务投影',
      '确认门禁',
      '监控负载',
      '退出托管',
    ]) {
      expect(within(businessChecks).getByText(label)).toBeVisible();
    }
    const stopAssistant = within(healthConsole).getByRole('button', {
      name: '停止助手',
    });
    expect(stopAssistant).toBeVisible();
    await user.click(stopAssistant);
    expect(mocks.save).toHaveBeenCalledWith({ enabled: false });
  });

  it('opens the selected candidate in the right-side inspector', async () => {
    const user = userEvent.setup();
    render(<HostedLimitUpBoardPage />);

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
    render(<HostedLimitUpBoardPage />);

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
    const { rerender } = render(<HostedLimitUpBoardPage />);
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
    rerender(<HostedLimitUpBoardPage />);

    const signalsTab = screen.getByRole('tab', { name: /待确认信号/ });
    await waitFor(() =>
      expect(signalsTab).toHaveAttribute('aria-selected', 'true')
    );
    expect(screen.getByText('600001.SH')).toBeVisible();

    await user.click(radarTab);
    mocks.assistantState.pendingIntents = [
      ...mocks.assistantState.pendingIntents,
    ];
    rerender(<HostedLimitUpBoardPage />);

    expect(radarTab).toHaveAttribute('aria-selected', 'true');
  });
});

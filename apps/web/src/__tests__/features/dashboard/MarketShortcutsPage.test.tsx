import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';

import { CORE_MARKET_INDICES } from '@/features/dashboard/marketWorkbench';
import MarketShortcutsPage from '@/features/dashboard/pages/MarketShortcutsPage';

const mocks = vi.hoisted(() => ({
  market: {} as Record<string, unknown>,
  now: new Date('2026-08-13T10:30:30+08:00'),
  pulse: {} as Record<string, unknown>,
}));

vi.mock('@/features/dashboard/components/MarketStudioShell', () => ({
  MarketStudioShell: ({ content }: { content: ReactNode }) => content,
}));

vi.mock('@/features/dashboard/components/MarketIntradayChart', () => ({
  MarketIntradayChart: () => <div data-testid="market-intraday-chart" />,
}));

vi.mock('@/features/dashboard/components/MarketStockSearch', () => ({
  MarketStockSearch: () => <div data-testid="market-stock-search" />,
}));

vi.mock('@/features/dashboard/hooks/useAMarketSession', () => ({
  useAMarketSession: () => ({
    calendarError: null,
    detail: '午间前持续交易',
    isOpen: true,
    isTradingDay: true,
    label: '交易中',
    now: mocks.now,
    phase: 'morning',
    targetTradingDate: '2026-08-13',
  }),
}));

vi.mock('@/features/dashboard/hooks/useMarketWorkbench', () => ({
  useMarketWorkbench: () => mocks.market,
}));

vi.mock('@/features/dashboard/hooks/useMarketPulse', () => ({
  useMarketPulse: () => mocks.pulse,
}));

vi.mock('@/features/strategies/hooks/useLimitUpRadar', () => ({
  useLimitUpRadar: () => ({
    candidates: [],
    error: null,
    fetching: false,
    industries: [],
    isScannerRunning: false,
    refresh: vi.fn(),
    summary: {
      brokenCount: 0,
      candidateCount: 0,
      nearLimitCount: 0,
      sealedCount: 0,
    },
  }),
}));

function createMarketState({
  dataMode,
  freshCoverage,
  latestQuoteAt,
  targetDateCoverage,
}: {
  dataMode: 'close' | 'intraday' | 'live';
  freshCoverage: number;
  latestQuoteAt: string | null;
  targetDateCoverage: number;
}) {
  return {
    dataMode,
    error: null,
    freshCoverage,
    indices: CORE_MARKET_INDICES.map(definition => ({
      definition,
      quote: null,
    })),
    latestQuoteAt,
    refreshLatestQuotes: vi.fn(),
    summary: {
      averageChange: null,
      coverage: CORE_MARKET_INDICES.length,
      tone: 'flat',
      toneLabel: '震荡',
    },
    targetDateCoverage,
  };
}

describe('MarketShortcutsPage realtime date guard', () => {
  beforeEach(() => {
    mocks.pulse = {
      breadth: { advancers: 0, decliners: 0, flats: 0, total: 0 },
      error: null,
      fetching: false,
      gainers: [],
      intraday: [],
      intradayRunning: false,
      losers: [],
      refresh: vi.fn(),
      snapshotAt: null,
      snapshotMode: 'unavailable',
    };
  });

  it('marks yesterday-only quotes as stale on the current trading day', () => {
    mocks.market = createMarketState({
      dataMode: 'close',
      freshCoverage: 0,
      latestQuoteAt: null,
      targetDateCoverage: 0,
    });

    render(<MarketShortcutsPage />);

    expect(screen.getAllByText('行情滞后').length).toBeGreaterThan(0);
    expect(screen.getByText('08/13 行情未入库')).toBeVisible();
    expect(screen.getByText('数据滞后')).toBeVisible();
    expect(screen.getAllByText(/08\/13 快照未入库/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/实时 08\/12/)).not.toBeInTheDocument();
    expect(screen.queryByText(/个股收盘 08\/12/)).not.toBeInTheDocument();
  });

  it('labels a fresh complete current-day snapshot as realtime', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });

    render(<MarketShortcutsPage />);

    expect(screen.getByText('实时 08/13 10:30:10')).toBeVisible();
    expect(screen.queryByText('数据滞后')).not.toBeInTheDocument();
  });

  it('keeps the index strip horizontally scrollable without a visible scrollbar', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });

    render(<MarketShortcutsPage />);

    const indexStrip = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[0].code}`
    ).parentElement;
    expect(indexStrip).not.toBeNull();
    expect(indexStrip).toHaveClass(
      'flex',
      'overflow-x-auto',
      'overscroll-x-contain',
      'no-scrollbar'
    );
    expect(indexStrip).not.toHaveClass('lg:grid-cols-6', 'lg:overflow-visible');
  });

  it('uses blue interaction styling when changing the selected market index', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });

    render(<MarketShortcutsPage />);

    const firstCard = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[0].code}`
    );
    const nextCard = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[1].code}`
    );

    expect(firstCard).toHaveAttribute('aria-pressed', 'true');
    expect(firstCard).toHaveClass(
      'ring-1',
      'ring-inset',
      'ring-blue-400/70',
      'focus-visible:ring-blue-400/70'
    );
    expect(firstCard).not.toHaveClass(
      'border-red-400/40',
      'bg-red-500/10',
      'bg-blue-500/10'
    );
    expect(firstCard.querySelector('.bg-blue-400')).toBeInTheDocument();
    expect(nextCard).not.toHaveClass('ring-1', 'ring-blue-400/70');

    fireEvent.click(nextCard);

    expect(firstCard).toHaveAttribute('aria-pressed', 'false');
    expect(nextCard).toHaveAttribute('aria-pressed', 'true');
    expect(nextCard).toHaveClass('ring-1', 'ring-blue-400/70');
    expect(nextCard.querySelector('.bg-blue-400')).toBeInTheDocument();
  });

  it('renders directional index surfaces and both real change fields without inventing missing values', () => {
    const positiveQuote = {
      change: 68.48,
      changePercent: 1.87,
      currentPrice: 3728.48,
      high: 3750,
      low: 3680,
      open: 3700,
      preClose: 3660,
      stockCode: CORE_MARKET_INDICES[0].code,
      time: '2026-08-13T10:30:10+08:00',
      volume: 100,
    };
    const negativeQuote = {
      ...positiveQuote,
      change: -8.89,
      changePercent: -0.46,
      currentPrice: 1913.93,
      stockCode: CORE_MARKET_INDICES[1].code,
    };
    mocks.market = {
      ...createMarketState({
        dataMode: 'live',
        freshCoverage: CORE_MARKET_INDICES.length,
        latestQuoteAt: '2026-08-13T10:30:10+08:00',
        targetDateCoverage: CORE_MARKET_INDICES.length,
      }),
      indices: CORE_MARKET_INDICES.map((definition, index) => ({
        definition,
        quote:
          index === 0 ? positiveQuote : index === 1 ? negativeQuote : null,
      })),
    };

    render(<MarketShortcutsPage />);

    const risingCard = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[0].code}`
    );
    const fallingCard = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[1].code}`
    );
    const unavailableCard = screen.getByTestId(
      `market-index-${CORE_MARKET_INDICES[2].code}`
    );

    expect(risingCard).toHaveAttribute('data-market-direction', 'up');
    expect(risingCard.getAttribute('style')).toContain('--market-up');
    expect(fallingCard).toHaveAttribute('data-market-direction', 'down');
    expect(fallingCard.getAttribute('style')).toContain('--market-down');
    expect(unavailableCard).toHaveAttribute('data-market-direction', 'flat');
    expect(unavailableCard.getAttribute('style')).toContain('--market-flat');
    expect(within(risingCard).getByText('3,728.48')).toHaveClass(
      'text-market-up'
    );
    expect(within(risingCard).getByText('+68.48')).toHaveClass('text-market-up');
    expect(within(risingCard).getByText('+1.87%')).toHaveClass(
      'text-market-up'
    );
    expect(within(fallingCard).getByText('1,913.93')).toHaveClass(
      'text-market-down'
    );
    expect(within(fallingCard).getByText('-8.89')).toHaveClass(
      'text-market-down'
    );
    expect(within(fallingCard).getByText('-0.46%')).toHaveClass(
      'text-market-down'
    );
    expect(within(unavailableCard).getAllByText('--')).toHaveLength(3);
    expect(
      within(unavailableCard).getAllByText('--').every(element =>
        element.classList.contains('text-market-flat')
      )
    ).toBe(true);
  });

  it('keeps the index-directory route as a compact terminal action tile', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });

    render(<MarketShortcutsPage />);

    const allIndices = screen.getByRole('link', {
      name: '打开全部指数目录',
    });
    expect(allIndices).toHaveAttribute('href', '/market/indices');
    expect(allIndices).toHaveClass(
      'h-28',
      'w-[5.75rem]',
      'flex-col',
      'items-center',
      'bg-slate-950/70',
      'focus-visible:ring-blue-400/70'
    );
    expect(within(allIndices).getByText('全部')).toBeVisible();
    expect(within(allIndices).getByText('指数')).toBeVisible();
    expect(allIndices.querySelector('svg')).toBeInTheDocument();
    expect(screen.queryByText('浏览完整目录')).not.toBeInTheDocument();
  });

  it('renders breadth advancers red and decliners green', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });
    mocks.pulse = {
      ...(mocks.pulse as object),
      breadth: { advancers: 1030, decliners: 3878, flats: 12, total: 4920 },
    };

    render(<MarketShortcutsPage />);

    const breadthCard = screen.getByText('涨跌家数对比').parentElement;
    expect(breadthCard).not.toBeNull();
    expect(breadthCard?.querySelector('.text-market-up')).toHaveTextContent(
      '1030'
    );
    expect(breadthCard?.querySelector('.text-market-down')).toHaveTextContent(
      '3878'
    );
  });

  it('renders gainers and losers as paired compact lists on PC layouts', () => {
    mocks.market = createMarketState({
      dataMode: 'live',
      freshCoverage: CORE_MARKET_INDICES.length,
      latestQuoteAt: '2026-08-13T10:30:10+08:00',
      targetDateCoverage: CORE_MARKET_INDICES.length,
    });
    mocks.pulse = {
      ...(mocks.pulse as object),
      gainers: [
        {
          changePct: 20.02,
          code: '300404.SZ',
          currentPrice: 14.45,
          name: '博济医药',
          volumeRatio: 2.13,
        },
      ],
      losers: [
        {
          changePct: -10.74,
          code: '300983.SZ',
          currentPrice: 38.38,
          name: '尤安设计',
          volumeRatio: 1.73,
        },
      ],
    };

    render(<MarketShortcutsPage />);

    expect(screen.getByTestId('stock-ranking-grid')).toHaveClass(
      'grid-cols-1',
      'sm:grid-cols-2',
      'gap-px'
    );
    const gainers = screen.getByTestId('stock-ranking-gainers');
    const losers = screen.getByTestId('stock-ranking-losers');
    expect(gainers.querySelector('ol')).toBeVisible();
    expect(losers.querySelector('ol')).toBeVisible();
    expect(gainers).not.toHaveClass('rounded-lg', 'border');
    expect(losers).not.toHaveClass('rounded-lg', 'border');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

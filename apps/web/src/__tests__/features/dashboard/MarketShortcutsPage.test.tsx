import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';

import { CORE_MARKET_INDICES } from '@/features/dashboard/marketWorkbench';
import MarketShortcutsPage from '@/features/dashboard/pages/MarketShortcutsPage';

const mocks = vi.hoisted(() => ({
  market: {} as Record<string, unknown>,
  now: new Date('2026-08-13T10:30:30+08:00'),
  pulse: {} as Record<string, unknown>,
}));

vi.mock('@/features/dashboard/components/DashboardStudioShell', () => ({
  DashboardStudioShell: ({ content }: { content: ReactNode }) => content,
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
      'grid-cols-[repeat(auto-fit,minmax(min(100%,22rem),1fr))]'
    );
    expect(screen.getByTestId('stock-ranking-gainers').querySelector('ol')).toBeVisible();
    expect(screen.getByTestId('stock-ranking-losers').querySelector('ol')).toBeVisible();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

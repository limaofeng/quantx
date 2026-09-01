import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TradingPage from '@/features/trading/pages/TradingPage';

const mocks = vi.hoisted(() => ({
  holdings: [
    {
      accountId: '300000013250',
      canUseVolume: 400,
      id: 'position-605499',
      instrumentName: '东鹏饮料',
      lastPrice: 123.86,
      stockCode: '605499.SH',
      volume: 400,
    },
    {
      accountId: '300000013250',
      canUseVolume: 9600,
      id: 'position-000543',
      instrumentName: '皖能电力',
      lastPrice: 7.35,
      stockCode: '000543.SZ',
      volume: 9600,
    },
  ],
  navigate: vi.fn(),
  search: '?symbol=605499.SH',
}));

vi.mock('wouter', () => ({
  useSearch: () => mocks.search,
}));

vi.mock('@/components/studio-workbench', () => ({
  StudioWorkbench: ({
    content,
    sidebar,
  }: {
    content: ReactNode;
    sidebar: ReactNode;
  }) => (
    <>
      {sidebar}
      {content}
    </>
  ),
}));

vi.mock('@/components/studio-workspace', () => ({
  useStudioNavigate: () => mocks.navigate,
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({
    data: {
      currentAccount: {
        accountName: '测试账户',
        cash: 50_000,
        frozenCash: 0,
        id: '300000013250',
        marketValue: 50_000,
        totalAsset: 100_000,
      },
    },
  }),
}));

vi.mock('@/features/portfolio/hooks/useHoldings', () => ({
  useHoldings: () => ({
    error: undefined,
    holdings: mocks.holdings,
    isLoading: false,
    portfolioSummary: { cash: 50_000 },
    refetch: vi.fn(),
  }),
}));

vi.mock('@/features/stocks/components', () => ({
  StockDetailWorkspace: ({
    activeView,
    initialSide,
  }: {
    activeView: string;
    initialSide?: string;
  }) => (
    <div
      data-testid="stock-workspace"
      data-active-view={activeView}
      data-initial-side={initialSide}
    />
  ),
}));

vi.mock('@/features/trading/components/TradingHoldingsSidebar', () => ({
  TradingHoldingsSidebar: ({
    onHoldingSelect,
  }: {
    onHoldingSelect: (holding: (typeof mocks.holdings)[number]) => void;
  }) => (
    <>
      <button type="button" onClick={() => onHoldingSelect(mocks.holdings[0])}>
        选择当前持仓
      </button>
      <button type="button" onClick={() => onHoldingSelect(mocks.holdings[1])}>
        选择其他持仓
      </button>
    </>
  ),
}));

vi.mock('@/features/trading/hooks', () => ({
  useTodayOrders: () => ({ orders: [] }),
}));

describe('TradingPage holding order side', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.search = '?symbol=605499.SH';
  });

  it('defaults the holdings order ticket to sell', () => {
    render(<TradingPage />);

    expect(screen.getByTestId('stock-workspace')).toHaveAttribute(
      'data-initial-side',
      'SELL'
    );
  });

  it('preserves an explicit buy entry', () => {
    mocks.search = '?symbol=605499.SH&side=BUY';

    render(<TradingPage />);

    expect(screen.getByTestId('stock-workspace')).toHaveAttribute(
      'data-initial-side',
      'BUY'
    );
  });

  it('switches a clicked holding to the sell order ticket', () => {
    mocks.search = '?symbol=605499.SH&side=BUY';
    render(<TradingPage />);

    fireEvent.click(screen.getByRole('button', { name: '选择当前持仓' }));

    expect(mocks.navigate).toHaveBeenCalledWith(
      '/holdings?symbol=605499.SH&side=SELL'
    );
    expect(screen.getByTestId('stock-workspace')).toHaveAttribute(
      'data-active-view',
      'ORDER'
    );
  });

  it('keeps sell intent when selecting a different holding', () => {
    render(<TradingPage />);

    fireEvent.click(screen.getByRole('button', { name: '选择其他持仓' }));

    expect(mocks.navigate).toHaveBeenCalledWith(
      '/holdings?symbol=000543.SZ&side=SELL'
    );
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Position } from '@/features/portfolio/types';
import { TradingCard } from '@/features/trading/components/TradingCard';
import type { Stock } from '@/shared/types';

const mocks = vi.hoisted(() => ({
  handleSubmit: vi.fn(),
  useHoldings: vi.fn(),
  useStockSearch: vi.fn(),
}));

vi.mock('@/features/portfolio/hooks/useHoldings', () => ({
  useHoldings: mocks.useHoldings,
}));

vi.mock('@/hooks/useStockSearch', () => ({
  useStockSearch: mocks.useStockSearch,
}));

vi.mock(
  '@/features/trading/components/TradingCard/hooks/useTradingSubmit',
  () => ({
    useTradingSubmit: () => ({
      handleSubmit: mocks.handleSubmit,
      isSubmitting: false,
    }),
  })
);

function makePosition(overrides: Partial<Position> = {}) {
  return {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 46.05,
    canUseVolume: 420,
    createdAt: '2026-06-17T09:30:00+08:00',
    direction: 1,
    frozenVolume: 0,
    id: 'position-688577',
    instrumentName: '浙海德曼',
    lastPrice: 48.7,
    marketValue: 20454,
    onRoadVolume: 0,
    openPrice: 49.66,
    profitLoss: 2249.52,
    profitRate: 12.35,
    stockCode: '688577.SH',
    updatedAt: '2026-06-17T10:59:36+08:00',
    volume: 10000,
    yesterdayVolume: 420,
    ...overrides,
  } as Position;
}

function makeStock(): Stock {
  return {
    id: '688577.SH',
    name: '浙海德曼',
    quote: {
      changePercent: -1.95,
      lastPrice: 48.7,
    },
    stockCode: '688577.SH',
  };
}

function setupTradingCard(position = makePosition()) {
  const selectedStock = makeStock();

  mocks.useHoldings.mockReturnValue({
    dailyAssetSnapshots: [],
    error: null,
    holdings: [position],
    isLoading: false,
    liquidateHolding: vi.fn(),
    portfolioSummary: {
      cash: 487300,
    },
    refetch: vi.fn(),
  });

  mocks.useStockSearch.mockReturnValue({
    filteredStocks: [selectedStock],
    handleStockSelect: vi.fn(),
    searchQuery: '',
    selectedStock,
    setSearchQuery: vi.fn(),
    stocksLoading: false,
  });

  render(<TradingCard />);
}

describe('TradingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.handleSubmit.mockImplementation(event => event.preventDefault());
  });

  it('uses sellable canUseVolume when filling a full close quantity', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    fireEvent.click(screen.getByRole('button', { name: '全仓' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(420);
    expect(screen.getByText('420')).toBeInTheDocument();
  });

  it('clamps manual close quantity to sellable canUseVolume', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '10000' },
    });

    expect(screen.getByPlaceholderText('100')).toHaveValue(420);
  });
});

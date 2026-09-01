import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Position } from '@/features/portfolio/types';
import { TradingCard } from '@/features/trading/components/TradingCard';
import type { Stock } from '@/shared/types';

const mocks = vi.hoisted(() => ({
  capabilities: {
    accountId: '300000013250',
    canLiveBuy: true,
    canLiveSell: true,
    canManualTrade: true,
    defaultExecutionMode: 'PAPER',
    executionModes: ['PAPER', 'LIVE'],
    instrumentCode: '688577.SH',
    liveBlockedReasons: [],
    liveReady: true,
    supportedPriceTypes: ['LIMIT', 'BEST'],
    supportedSides: ['BUY', 'SELL'],
    warnings: [],
  },
  handleSubmit: vi.fn(),
  onQueued: vi.fn(),
  useStockSearch: vi.fn(),
}));

vi.mock('@/hooks/useStockSearch', () => ({
  useStockSearch: mocks.useStockSearch,
}));

vi.mock(
  '@/features/trading/components/TradingCard/hooks/useTradingSubmit',
  () => ({
    useTradingSubmit: (_instrumentCode: string, onQueued?: () => void) => {
      mocks.onQueued.mockImplementation(() => onQueued?.());
      return {
        capabilities: mocks.capabilities,
        capabilitiesError: null,
        capabilitiesLoading: false,
        confirmationError: '',
        confirmPreview: vi.fn(),
        dismissPreview: vi.fn(),
        handleSubmit: mocks.handleSubmit,
        isConfirming: false,
        isPreviewing: false,
        preview: null,
      };
    },
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

function setupTradingCard(
  position = makePosition(),
  portfolioSummary = { cash: 487300 }
) {
  const selectedStock = makeStock();

  mocks.useStockSearch.mockReturnValue({
    filteredStocks: [selectedStock],
    handleStockSelect: vi.fn(),
    searchQuery: '',
    selectedStock,
    setSearchQuery: vi.fn(),
    stocksLoading: false,
  });

  render(
    <TradingCard holdings={[position]} portfolioSummary={portfolioSummary} />
  );
}

describe('TradingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(mocks.capabilities, {
      canLiveBuy: true,
      canLiveSell: true,
      canManualTrade: true,
      defaultExecutionMode: 'PAPER',
      executionModes: ['PAPER', 'LIVE'],
      liveBlockedReasons: [],
      liveReady: true,
    });
    mocks.handleSubmit.mockImplementation(event => event.preventDefault());
  });

  it('uses sellable canUseVolume when filling a full close quantity', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    fireEvent.click(screen.getByRole('button', { name: '全仓' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(420);
    expect(screen.getByText('420')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全仓' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('fills valid board-lot quantities from buy shortcuts', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '1/4' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(2500);
    expect(screen.getByRole('button', { name: '1/4' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    fireEvent.click(screen.getByRole('button', { name: '1W' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(200);
    expect(screen.getByRole('button', { name: '1W' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('disables shortcuts with an explicit reason when no quantity is available', () => {
    setupTradingCard(makePosition(), { cash: 0 });

    for (const label of ['1/4', '1/2', '全仓', '1W']) {
      expect(screen.getByRole('button', { name: label })).toBeDisabled();
      expect(screen.getByRole('button', { name: label })).toHaveAttribute(
        'title',
        '可用资金不足，无法填写委托数量'
      );
    }
  });

  it('clears a quantity when switching trading direction', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    fireEvent.click(screen.getByRole('button', { name: '全仓' }));
    fireEvent.click(screen.getByRole('button', { name: '买入' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(null);
  });

  it('leaves manual quantity normalization to the server preview', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '10000' },
    });

    expect(screen.getByPlaceholderText('100')).toHaveValue(10000);
  });

  it('uses the financial buy and sell action colors', () => {
    setupTradingCard();

    expect(screen.getByRole('button', { name: '获取买入预览' })).toHaveClass(
      'bg-market-buy-cta',
      'text-white'
    );

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));

    expect(screen.getByRole('button', { name: '获取平仓预览' })).toHaveClass(
      'bg-market-down',
      'text-white'
    );
  });

  it('uses the PAPER capability default and preserves explicit mode changes', () => {
    setupTradingCard();

    expect(screen.getByRole('button', { name: 'PAPER 模拟' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByRole('button', { name: 'LIVE 实盘' })).toHaveAttribute(
      'aria-pressed',
      'false'
    );

    fireEvent.click(screen.getByRole('button', { name: 'LIVE 实盘' }));

    expect(screen.getByRole('button', { name: 'LIVE 实盘' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByText(/高风险：确认后进入实盘执行链/)).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));

    expect(screen.getByRole('button', { name: 'PAPER 模拟' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    fireEvent.click(screen.getByRole('button', { name: 'LIVE 实盘' }));
    act(() => mocks.onQueued());

    expect(screen.getByRole('button', { name: 'PAPER 模拟' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('follows the LIVE capability default across direction and queue resets', () => {
    Object.assign(mocks.capabilities, { defaultExecutionMode: 'LIVE' });
    setupTradingCard();

    expect(screen.getByText('默认 LIVE')).toBeVisible();
    expect(screen.getByRole('button', { name: 'LIVE 实盘' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    fireEvent.click(screen.getByRole('button', { name: 'PAPER 模拟' }));
    expect(screen.getByRole('button', { name: 'PAPER 模拟' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    fireEvent.click(screen.getByRole('button', { name: '平仓' }));
    expect(screen.getByRole('button', { name: 'LIVE 实盘' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    act(() => mocks.onQueued());
    expect(screen.getByRole('button', { name: 'LIVE 实盘' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('submits a preview request instead of placing an order directly', () => {
    setupTradingCard();

    fireEvent.change(screen.getByPlaceholderText('0.00'), {
      target: { value: '48.76' },
    });
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '420' },
    });
    fireEvent.click(screen.getByRole('button', { name: '获取买入预览' }));

    expect(mocks.handleSubmit).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        executionMode: 'PAPER',
        orderType: 'limit',
        price: '48.76',
        quantity: '420',
        tradeType: 'buy',
      })
    );
  });

  it('does not rehydrate an unchanged zero-price initial holding', () => {
    const position = makePosition({ lastPrice: 0 });
    const selectedStock = {
      ...makeStock(),
      quote: {
        changePercent: position.profitRate,
        lastPrice: 0,
      },
    };
    const handleStockSelect = vi.fn();
    mocks.useStockSearch.mockReturnValue({
      filteredStocks: [selectedStock],
      handleStockSelect,
      searchQuery: '',
      selectedStock,
      setSearchQuery: vi.fn(),
      stocksLoading: false,
    });

    render(
      <TradingCard
        holdings={[position]}
        initialStockCode={position.stockCode}
        portfolioSummary={{ cash: 487300 }}
      />
    );

    expect(handleStockSelect).not.toHaveBeenCalled();
  });
});

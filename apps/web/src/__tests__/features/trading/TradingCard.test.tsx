import { fireEvent, render, screen } from '@testing-library/react';
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
    defaultExecutionMode: 'LIVE',
    executionModes: ['LIVE'],
    instrumentCode: '688577.SH',
    liveBlockedReasons: [],
    liveReady: true,
    supportedPriceTypes: ['LIMIT', 'BEST'],
    supportedSides: ['BUY', 'SELL'],
    warnings: [],
  },
  handleSubmit: vi.fn(),
  useStockSearch: vi.fn(),
}));

vi.mock('@/hooks/useStockSearch', () => ({
  useStockSearch: mocks.useStockSearch,
}));

vi.mock(
  '@/features/trading/components/TradingCard/hooks/useTradingSubmit',
  () => ({
    useTradingSubmit: () => {
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
        orderAttempt: null,
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
  portfolioSummary = { cash: 487300 },
  options: {
    initialSide?: 'BUY' | 'SELL';
    initialStockCode?: string;
  } = {}
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

  return render(
    <TradingCard
      holdings={[position]}
      initialSide={options.initialSide}
      initialStockCode={options.initialStockCode}
      portfolioSummary={portfolioSummary}
    />
  );
}

describe('TradingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(mocks.capabilities, {
      canLiveBuy: true,
      canLiveSell: true,
      canManualTrade: true,
      defaultExecutionMode: 'LIVE',
      executionModes: ['LIVE'],
      liveBlockedReasons: [],
      liveReady: true,
    });
    mocks.handleSubmit.mockImplementation(event => event.preventDefault());
  });

  it('starts from the requested sell side', () => {
    const position = makePosition();
    setupTradingCard(
      position,
      { cash: 487300 },
      {
        initialSide: 'SELL',
        initialStockCode: position.stockCode,
      }
    );

    expect(screen.getByRole('button', { name: '卖出' })).toHaveClass(
      'text-white'
    );
    expect(
      screen.getByRole('button', { name: '获取卖出预览' })
    ).toBeInTheDocument();
  });

  it('restores the requested sell side when the selected holding changes', () => {
    const position = makePosition();
    const view = setupTradingCard(
      position,
      { cash: 487300 },
      {
        initialSide: 'SELL',
        initialStockCode: position.stockCode,
      }
    );

    fireEvent.click(screen.getByRole('button', { name: '买入' }));
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '100' },
    });

    view.rerender(
      <TradingCard
        holdings={[position]}
        initialSide="SELL"
        initialStockCode="000543.SZ"
        portfolioSummary={{ cash: 487300 }}
      />
    );

    expect(screen.getByRole('button', { name: '卖出' })).toHaveClass(
      'text-white'
    );
    expect(screen.getByPlaceholderText('100')).toHaveValue(null);
  });

  it('uses sellable canUseVolume when filling a full close quantity', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '卖出' }));
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

    fireEvent.click(screen.getByRole('button', { name: '卖出' }));
    fireEvent.click(screen.getByRole('button', { name: '全仓' }));
    fireEvent.click(screen.getByRole('button', { name: '买入' }));

    expect(screen.getByPlaceholderText('100')).toHaveValue(null);
  });

  it('leaves manual quantity normalization to the server preview', () => {
    setupTradingCard();

    fireEvent.click(screen.getByRole('button', { name: '卖出' }));
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

    fireEvent.click(screen.getByRole('button', { name: '卖出' }));

    expect(screen.getByRole('button', { name: '获取卖出预览' })).toHaveClass(
      'bg-market-down',
      'text-white'
    );
  });

  it('does not expose a manual PAPER or LIVE switch', () => {
    setupTradingCard();

    expect(screen.getByText('LIVE')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'PAPER 模拟' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'LIVE 实盘' })
    ).not.toBeInTheDocument();
    expect(screen.queryByText('执行模式')).not.toBeInTheDocument();
  });

  it('keeps configured LIVE visible when the selected direction is blocked', () => {
    Object.assign(mocks.capabilities, {
      canLiveSell: false,
      defaultExecutionMode: 'LIVE',
      executionModes: ['LIVE'],
      liveBlockedReasons: ['实盘安全门禁暂未就绪'],
    });
    setupTradingCard();

    expect(screen.getByText('LIVE')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '卖出' }));
    expect(screen.getByText('LIVE')).toBeVisible();
    expect(screen.queryByText('PAPER')).not.toBeInTheDocument();
    expect(screen.getByText(/LIVE 下单已阻止/)).toBeVisible();
    expect(screen.getByRole('button', { name: '获取卖出预览' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '买入' }));
    fireEvent.change(screen.getByPlaceholderText('0.00'), {
      target: { value: '48.76' },
    });
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '420' },
    });
    fireEvent.click(screen.getByRole('button', { name: '获取买入预览' }));

    expect(mocks.handleSubmit).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ executionMode: 'LIVE', tradeType: 'buy' })
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
        executionMode: 'LIVE',
        orderType: 'limit',
        price: '48.76',
        quantity: '420',
        tradeType: 'buy',
      })
    );
  });

  it('uses PAPER only when the server reports a non-live runtime', () => {
    Object.assign(mocks.capabilities, {
      canLiveBuy: false,
      canLiveSell: false,
      defaultExecutionMode: 'PAPER',
      executionModes: ['PAPER'],
      liveBlockedReasons: [],
      liveReady: false,
    });
    setupTradingCard();

    expect(screen.getByText('PAPER')).toBeVisible();

    fireEvent.change(screen.getByPlaceholderText('0.00'), {
      target: { value: '48.76' },
    });
    fireEvent.change(screen.getByPlaceholderText('100'), {
      target: { value: '420' },
    });
    fireEvent.click(screen.getByRole('button', { name: '获取买入预览' }));

    expect(mocks.handleSubmit).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ executionMode: 'PAPER', tradeType: 'buy' })
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

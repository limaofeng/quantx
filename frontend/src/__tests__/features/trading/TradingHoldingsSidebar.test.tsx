import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Position } from '@/features/portfolio/types';
import { TradingHoldingsSidebar } from '@/features/trading/components/TradingHoldingsSidebar';

vi.mock('@/features/portfolio/hooks/useRealTimeHoldings', () => ({
  useRealTimeHoldings: ({ holdings }: { holdings: Position[] }) => ({
    error: null,
    holdings,
    isConnected: true,
  }),
}));

function makePosition(overrides: Partial<Position> = {}) {
  return {
    accountId: 'account-1',
    accountType: 'STOCK',
    avgPrice: 106.2,
    canUseVolume: 5500,
    changePercent: 1.23,
    createdAt: '2026-06-04T09:30:00',
    direction: 'LONG',
    frozenVolume: 0,
    id: 'position-1',
    instrumentName: '比亚迪',
    lastPrice: 94.19,
    marketValue: 518045,
    marketValuePercent: 12.3,
    onRoadVolume: 0,
    openPrice: 0,
    profitLoss: -66000,
    profitRate: -11.3,
    stockCode: '002594.SZ',
    updatedAt: '2026-06-04T10:00:00',
    volume: 5500,
    yesterdayVolume: 5500,
    ...overrides,
  } as Position;
}

describe('TradingHoldingsSidebar', () => {
  it('shows daily quote change percent separately from holding return', () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[makePosition()]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButton = screen.getByRole('button', { name: /比亚迪/ });
    const stockCode = within(holdingButton).getByText('002594.SZ');
    const dayReturn = within(holdingButton).getByText('+1.23%');

    expect(dayReturn).toBeInTheDocument();
    expect(stockCode.parentElement).not.toHaveTextContent('+1.23%');
    expect(dayReturn.closest('.text-right')).not.toBeNull();
    expect(within(holdingButton).getByText('持有收益')).toBeInTheDocument();
    expect(within(holdingButton).getByText('-11.30%')).toBeInTheDocument();
  });

  it('places profit loss underneath holding return in the compact metrics', () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[makePosition()]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButton = screen.getByRole('button', { name: /比亚迪/ });
    const metricLabels = Array.from(
      holdingButton.lastElementChild?.querySelectorAll('span') ?? []
    )
      .map(element => element.textContent)
      .filter((text): text is string =>
        ['数量', '可用', '持有收益', '成本额', '市值', '盈亏'].includes(
          text ?? ''
        )
      );

    expect(metricLabels).toEqual([
      '数量',
      '可用',
      '持有收益',
      '成本额',
      '市值',
      '盈亏',
    ]);
    expect(
      metricLabels.indexOf('盈亏') - metricLabels.indexOf('持有收益')
    ).toBe(3);
  });

  it('shows current and cost prices in the row header', () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[makePosition()]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButton = screen.getByRole('button', { name: /比亚迪/ });

    expect(within(holdingButton).getByText('现价')).toBeInTheDocument();
    expect(within(holdingButton).getByText('¥94.19')).toBeInTheDocument();
    expect(within(holdingButton).getByText('成本')).toBeInTheDocument();
    expect(within(holdingButton).getByText('¥106.20')).toBeInTheDocument();
  });

  it('shows market value and cost amount in the compact holding row', () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[makePosition()]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButton = screen.getByRole('button', { name: /比亚迪/ });

    expect(within(holdingButton).getByText('成本额')).toBeInTheDocument();
    expect(within(holdingButton).getByText('¥58.41万')).toBeInTheDocument();
    expect(within(holdingButton).getByText('市值')).toBeInTheDocument();
    expect(within(holdingButton).getByText('¥51.80万')).toBeInTheDocument();
  });
});

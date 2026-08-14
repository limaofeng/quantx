import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Position } from '@/features/portfolio/types';
import { resolveHoldingInstrumentName } from '@/features/trading/components/instrumentNameUtils';
import { TradingHoldingsSidebar } from '@/features/trading/components/TradingHoldingsSidebar';

vi.mock('@/features/portfolio/hooks/useRealTimeHoldings', () => ({
  useRealTimeHoldings: ({ holdings }: { holdings: Position[] }) => ({
    error: null,
    holdings,
    isConnected: true,
  }),
}));

const SORT_PREFERENCE_STORAGE_KEY =
  'quantx.tradingHoldingsSidebar.sortPreference.v1';

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

function getRenderedHoldingButtons() {
  return screen
    .getAllByRole('button')
    .filter(button =>
      ['比亚迪', '长江电力'].some(name => button.textContent?.includes(name))
    );
}

async function openSortMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: '选择持仓排序' }));
  await screen.findByRole('menu');
}

describe('TradingHoldingsSidebar', () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.mocked(window.localStorage.getItem).mockImplementation(
      key => storage.get(String(key)) ?? null
    );
    vi.mocked(window.localStorage.setItem).mockImplementation((key, value) => {
      storage.set(String(key), String(value));
    });
    vi.mocked(window.localStorage.removeItem).mockImplementation(key => {
      storage.delete(String(key));
    });
    vi.mocked(window.localStorage.clear).mockImplementation(() => {
      storage.clear();
    });
  });

  it('uses the catalog name when a position snapshot repeats the stock code', () => {
    expect(
      resolveHoldingInstrumentName('688552.SH', '688552.SH', '航天南湖')
    ).toBe('航天南湖');
    expect(
      resolveHoldingInstrumentName('302132.SZ', '302132', '中航成飞')
    ).toBe('中航成飞');
  });

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
    expect(dayReturn).toHaveClass('text-market-up');
    expect(within(holdingButton).getByText('持有收益')).toBeInTheDocument();
    expect(within(holdingButton).getByText('-11.30%')).toHaveClass(
      'text-holding-down'
    );
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

  it('keeps market value sorting as the default', () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[
          makePosition({
            id: 'position-low',
            instrumentName: '长江电力',
            marketValue: 100000,
            stockCode: '600900.SH',
          }),
          makePosition(),
        ]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButtons = getRenderedHoldingButtons();
    expect(holdingButtons[0]).toHaveTextContent('比亚迪');
    expect(holdingButtons[1]).toHaveTextContent('长江电力');
  });

  it('uses the persisted manual holding order when manual sorting is active', () => {
    window.localStorage.setItem(
      SORT_PREFERENCE_STORAGE_KEY,
      JSON.stringify({
        manualOrder: ['600900.SH', '002594.SZ'],
        sortKey: 'MANUAL',
      })
    );

    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[
          makePosition(),
          makePosition({
            id: 'position-low',
            instrumentName: '长江电力',
            marketValue: 100000,
            stockCode: '600900.SH',
          }),
        ]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    const holdingButtons = getRenderedHoldingButtons();
    expect(holdingButtons[0]).toHaveTextContent('长江电力');
    expect(holdingButtons[1]).toHaveTextContent('比亚迪');
    expect(
      screen.getByRole('button', { name: '选择持仓排序' })
    ).toHaveTextContent('手动');
    expect(
      screen.queryByRole('button', { name: '设置手动排序' })
    ).not.toBeInTheDocument();
  });

  it('sorts holdings by selected profit field', async () => {
    render(
      <TradingHoldingsSidebar
        accountName="账户300000013250"
        holdings={[
          makePosition(),
          makePosition({
            id: 'position-profit',
            instrumentName: '长江电力',
            marketValue: 100000,
            profitLoss: 1200,
            stockCode: '600900.SH',
          }),
        ]}
        isLoading={false}
        onAccountOpen={vi.fn()}
        onHoldingSelect={vi.fn()}
        onRefresh={vi.fn()}
        onStockInfoOpen={vi.fn()}
      />
    );

    await openSortMenu();
    fireEvent.click(screen.getByRole('menuitemradio', { name: '持仓盈亏' }));

    let holdingButtons = getRenderedHoldingButtons();
    expect(holdingButtons[0]).toHaveTextContent('长江电力');
    expect(holdingButtons[1]).toHaveTextContent('比亚迪');

    await openSortMenu();
    fireEvent.click(screen.getByRole('menuitemradio', { name: '升序优先' }));

    holdingButtons = getRenderedHoldingButtons();
    expect(holdingButtons[0]).toHaveTextContent('比亚迪');
    expect(holdingButtons[1]).toHaveTextContent('长江电力');
  });

  it('switches into manual sorting and opens manual order settings', async () => {
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

    await openSortMenu();
    fireEvent.click(screen.getByRole('menuitemradio', { name: '手动排序' }));

    expect(
      screen.getByRole('button', { name: '选择持仓排序' })
    ).toHaveTextContent('手动');
    await openSortMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: '编辑手动顺序' }));

    expect(
      screen.getByRole('heading', { name: '设置手动排序' })
    ).toBeInTheDocument();
    expect(screen.getByLabelText('拖拽持仓 比亚迪')).toBeInTheDocument();
    expect(window.localStorage.getItem(SORT_PREFERENCE_STORAGE_KEY)).toContain(
      '"sortKey":"MANUAL"'
    );
  });
});

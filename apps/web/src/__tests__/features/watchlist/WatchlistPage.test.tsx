import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WatchlistPage from '@/features/watchlist/pages/WatchlistPage';

const setLocation = vi.fn();
const saveItem = vi.fn().mockResolvedValue({ success: true, message: 'ok' });
const refetch = vi.fn();
const watchlistFixture = vi.hoisted(() => ({
  groups: [] as Array<{
    displayOrder: number;
    id: string;
    itemCount: number;
    name: string;
  }>,
  items: [
    {
      displayOrder: 0,
      groupMemberships: [] as Array<{ displayOrder: number; groupId: string }>,
      groups: [] as Array<{
        displayOrder: number;
        id: string;
        itemCount: number;
        name: string;
      }>,
      id: 'item-1',
      instrumentName: '平安银行',
      stockCode: '000001.SZ',
    },
  ],
  location: '/watchlist?collection=all&symbol=000001.SZ',
  reorderGroupItems: vi.fn().mockResolvedValue({ success: true, message: 'ok' }),
  searchResults: [
    { id: 'item-search', name: 'Search Stock', stockCode: '000003.SZ' },
  ],
}));

vi.mock('wouter', () => ({
  useLocation: () => [
    watchlistFixture.location,
    setLocation,
  ],
}));

vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({
    confirm: vi.fn().mockResolvedValue(true),
  }),
}));

vi.mock('@/components/studio-workbench/sidebarSizing', () => ({
  useStudioSidebarSizing: () => ({
    handleSidebarResizeKeyDown: vi.fn(),
    handleSidebarResizeStart: vi.fn(),
    sidebarWidth: 360,
  }),
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({
    data: {
      currentAccount: {
        accountName: '测试账户',
        cash: 100000,
        frozenCash: 0,
        id: 'account-1',
        marketValue: 120000,
        totalAsset: 220000,
        totalProfitLoss: 1200,
      },
    },
  }),
}));

vi.mock('@/features/portfolio/hooks/useHoldings', () => ({
  useHoldings: () => ({
    holdings: [
      {
        instrumentName: '平安银行',
        lastPrice: 10.5,
        profitLoss: 120,
        profitRate: 1.2,
        stockCode: '000001.SZ',
        volume: 1000,
      },
    ],
    portfolioSummary: undefined,
  }),
}));

vi.mock('@/features/portfolio/hooks/useRealTimeHoldings', () => ({
  useLatestMarketQuotes: () => ({
    error: undefined,
    isConnected: true,
    quotes: new Map([
      [
        '000001.SZ',
        {
          changePercent: 1.2,
          high: 10.7,
          lastPrice: 10.5,
          low: 10.1,
          open: 10.2,
          preClose: 10.3,
          time: new Date().toISOString(),
        },
      ],
    ]),
    refreshLatestQuotes: vi.fn(),
  }),
}));

vi.mock('@/features/stocks/components', () => ({
  StockDetailWorkspace: () => (
    <div data-testid="stock-detail-workspace">详情</div>
  ),
}));

vi.mock('@/features/stocks/hooks/useStockDetail', () => ({
  useStockDetail: () => ({
    error: null,
    isLoading: false,
    refetch: vi.fn(),
    stock: {
      id: '000001.SZ',
      market: 'SZ',
      name: '平安银行',
      quote: { lastPrice: 10.5 },
      type: 'STOCK',
    },
  }),
}));

vi.mock('@/hooks/useStockSearch', () => ({
  useStockSearch: () => ({
    filteredStocks: watchlistFixture.searchResults,
    setSearchQuery: vi.fn(),
    stocksLoading: false,
  }),
}));

vi.mock('@/features/watchlist/hooks', () => ({
  normalizeWatchlistCode: (value: string) => value.trim().toUpperCase(),
  useWatchlistWorkspace: () => ({
    codes: ['000001.SZ'],
    createGroup: vi.fn(),
    deleteGroup: vi.fn(),
    error: undefined,
    fetching: false,
    groups: watchlistFixture.groups,
    isStale: false,
    items: watchlistFixture.items,
    refetch,
    removeItem: vi.fn(),
    renameGroup: vi.fn(),
    reorderGroupItems: watchlistFixture.reorderGroupItems,
    reorderGroups: vi.fn(),
    reorderItems: vi.fn(),
    saveItem,
  }),
}));

describe('WatchlistPage', () => {
  beforeEach(() => {
    setLocation.mockClear();
    saveItem.mockClear();
    watchlistFixture.location = '/watchlist?collection=all&symbol=000001.SZ';
    watchlistFixture.groups = [];
    watchlistFixture.searchResults = [
      { id: 'item-search', name: 'Search Stock', stockCode: '000003.SZ' },
    ];
    watchlistFixture.items = [
      {
        displayOrder: 0,
        groupMemberships: [],
        groups: [],
        id: 'item-1',
        instrumentName: '平安银行',
        stockCode: '000001.SZ',
      },
    ];
    watchlistFixture.reorderGroupItems.mockClear();
  });

  it('renders the master list and keeps the shared detail workspace in place', () => {
    render(<WatchlistPage />);

    expect(screen.getByTestId('watchlist-page')).toBeInTheDocument();
    expect(screen.getByTestId('watchlist-row-000001.SZ')).toBeInTheDocument();
    expect(screen.getByTestId('stock-detail-workspace')).toBeInTheDocument();
  });

  it('keeps master selection in the watchlist URL while rendering detail in place', () => {
    render(<WatchlistPage />);

    fireEvent.click(
      screen.getByRole('button', { name: '选择 平安银行 000001.SZ' })
    );

    expect(setLocation).toHaveBeenCalledWith(
      '?collection=all&symbol=000001.SZ'
    );
  });

  it('collapses and reopens the left browser without changing the route', () => {
    render(<WatchlistPage />);

    fireEvent.click(
      screen.getAllByRole('button', { name: '折叠自选浏览器' }).at(-1)!
    );
    expect(screen.queryByTestId('watchlist-sidebar')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开自选浏览器' }));
    expect(screen.getByTestId('watchlist-sidebar')).toBeInTheDocument();
    expect(saveItem).not.toHaveBeenCalled();
  });

  it('renders custom groups by membership order and moves using that order', async () => {
    watchlistFixture.location =
      '/watchlist?collection=group-1&symbol=000002.SZ';
    watchlistFixture.groups = [
      { displayOrder: 0, id: 'group-1', itemCount: 2, name: 'Group A' },
    ];
    watchlistFixture.items = [
      {
        displayOrder: 0,
        groupMemberships: [{ displayOrder: 1, groupId: 'group-1' }],
        groups: [
          { displayOrder: 0, id: 'group-1', itemCount: 2, name: 'Group A' },
        ],
        id: 'item-a',
        instrumentName: 'Stock A',
        stockCode: '000001.SZ',
      },
      {
        displayOrder: 1,
        groupMemberships: [{ displayOrder: 0, groupId: 'group-1' }],
        groups: [
          { displayOrder: 0, id: 'group-1', itemCount: 2, name: 'Group A' },
        ],
        id: 'item-b',
        instrumentName: 'Stock B',
        stockCode: '000002.SZ',
      },
    ];

    render(<WatchlistPage />);

    expect(
      screen
        .getAllByTestId(/watchlist-row-/)
        .map(row => row.getAttribute('data-testid'))
    ).toEqual(['watchlist-row-000002.SZ', 'watchlist-row-000001.SZ']);

    fireEvent.click(
      screen.getByRole('button', { name: '将 Stock B 下移' })
    );

    await waitFor(() => {
      expect(watchlistFixture.reorderGroupItems).toHaveBeenCalledWith({
        groupId: 'group-1',
        itemIds: ['item-a', 'item-b'],
      });
    });
  });

  it('preserves existing groups when search adds an item to a custom group', async () => {
    watchlistFixture.location =
      '/watchlist?collection=group-1&symbol=000003.SZ';
    watchlistFixture.groups = [
      { displayOrder: 0, id: 'group-1', itemCount: 1, name: 'Group A' },
    ];
    watchlistFixture.items = [
      {
        displayOrder: 0,
        groupMemberships: [{ displayOrder: 0, groupId: 'group-1' }],
        groups: [
          {
            displayOrder: 0,
            id: 'group-legacy',
            itemCount: 1,
            name: 'Legacy Group',
          },
          { displayOrder: 0, id: 'group-1', itemCount: 1, name: 'Group A' },
        ],
        id: 'item-search',
        instrumentName: 'Search Stock',
        stockCode: '000003.SZ',
      },
    ];

    render(<WatchlistPage />);
    fireEvent.change(screen.getByRole('textbox', { name: '搜索并添加股票' }), {
      target: { value: 'Search' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Search Stock000003.SZ' })
    );

    await waitFor(() => {
      expect(saveItem).toHaveBeenCalledWith({
        groupIds: ['group-legacy', 'group-1'],
        instrumentName: 'Search Stock',
        stockCode: '000003.SZ',
      });
    });
  });
});

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LiquidationPage } from '@/features/portfolio/pages/LiquidationPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  setLocation: vi.fn(),
  refetch: vi.fn(),
  refetchConditionalOrders: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('wouter', () => ({
  useLocation: () => ['/liquidation?symbol=300917.SZ', mocks.setLocation],
  useSearch: () => '?symbol=300917.SZ',
}));

vi.mock('urql', () => ({
  useMutation: () => [{ fetching: false }, vi.fn()],
  useQuery: () => [
    {
      data: { conditionalLiquidationOrders: [] },
      error: undefined,
      fetching: false,
    },
    mocks.refetchConditionalOrders,
  ],
}));

vi.mock('@/components/studio-workbench', () => ({
  StudioWorkbench: ({
    content,
    showSidebar,
    sidebar,
  }: {
    content: ReactNode;
    showSidebar?: boolean;
    sidebar?: ReactNode;
  }) => (
    <>
      {showSidebar && sidebar ? (
        <div data-testid="workspace-sidebar">{sidebar}</div>
      ) : null}
      {content}
    </>
  ),
}));

vi.mock('@/components/studio-workspace', () => ({
  useStudioNavigate: () => mocks.navigate,
}));

vi.mock('@/features/trading-safety', () => ({
  ExecutionHealthSidebar: () => <aside>卖出执行健康</aside>,
}));

vi.mock('@/features/portfolio/components/SellManagementPanels', () => ({
  ExitPlansPanel: () => <div>全部计划内容</div>,
  PositionLiquidationPanel: () => <div>持仓清仓内容</div>,
  SellHistoryPanel: () => <div>卖出记录内容</div>,
}));

vi.mock('@/features/portfolio/components/TakeProfitPlanPanel', () => ({
  TakeProfitPlanPanel: () => null,
}));

vi.mock('@/features/portfolio/components/ExitPlanReplayPanel', () => ({
  ExitPlanReplayPanel: () => <div>回放测试内容</div>,
}));

vi.mock('@/features/portfolio/hooks/useLiquidationActions', () => ({
  useLiquidationActions: () => ({
    error: undefined,
    isLoading: false,
    liquidateMultiple: vi.fn(),
  }),
}));

vi.mock('@/features/portfolio/hooks/useLiquidationData', () => ({
  useLiquidationData: () => ({
    accountId: '300000013250',
    currentHoldings: [
      {
        avgPrice: 18.6,
        canUseVolume: 800,
        frozenVolume: 0,
        instrumentName: '测试标的',
        lastPrice: 22.4,
        marketValue: 22_400,
        onRoadVolume: 0,
        stockCode: '300917.SZ',
        volume: 1000,
        yesterdayVolume: 1000,
      },
      {
        avgPrice: 9.8,
        canUseVolume: 600,
        frozenVolume: 0,
        instrumentName: '示例科技',
        lastPrice: 10.2,
        marketValue: 6120,
        onRoadVolume: 0,
        stockCode: '000001.SZ',
        volume: 600,
        yesterdayVolume: 600,
      },
    ],
    error: undefined,
    isLoading: false,
    liquidatedStocks: [],
    portfolioSummary: {
      accountName: '测试账户',
      totalAsset: 100_000,
    },
    refetch: mocks.refetch,
    todayOrders: [],
    todayTrades: [],
  }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

describe('LiquidationPage overview navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('returns from a stock workspace to all exit plans', async () => {
    const user = userEvent.setup();
    render(<LiquidationPage />);

    await user.click(screen.getByRole('button', { name: '返回全部卖出计划' }));

    expect(mocks.setLocation).toHaveBeenCalledWith('/liquidation');
    expect(screen.getByText('全部计划内容')).toBeVisible();
  });

  it('keeps first- and second-level tabs in the same toolbar row', async () => {
    const user = userEvent.setup();
    render(<LiquidationPage />);

    const navigation = screen.getByRole('navigation', { name: '卖出工作区' });
    expect(within(navigation).getByText('卖出管理')).toBeVisible();
    expect(within(navigation).getByText('回放测试')).toBeVisible();
    expect(within(navigation).getByText('卖出计划')).toBeVisible();
    expect(within(navigation).getByText('持仓清仓')).toBeVisible();
    expect(within(navigation).getByText('卖出记录')).toBeVisible();

    await user.click(within(navigation).getByText('回放测试'));

    expect(screen.getByText('回放测试内容')).toBeVisible();
    expect(within(navigation).queryByText('卖出计划')).not.toBeInTheDocument();
    expect(mocks.setLocation).toHaveBeenCalledWith(
      '/liquidation?symbol=300917.SZ&workspace=REPLAY'
    );
  });

  it('docks execution health in the left workspace instead of a holdings sidebar', () => {
    render(<LiquidationPage />);

    expect(screen.getByTestId('workspace-sidebar')).toHaveTextContent(
      '卖出执行健康'
    );
    expect(
      screen.queryByRole('button', { name: /执行健康 ·/ })
    ).not.toBeInTheDocument();
  });

  it('selects a sell target from the compact searchable selector', async () => {
    const user = userEvent.setup();
    render(<LiquidationPage />);

    await user.click(screen.getByRole('combobox', { name: '选择卖出标的' }));
    await user.type(
      screen.getByRole('combobox', { name: '搜索持仓股票' }),
      '示例科技'
    );
    await user.click(screen.getByText('示例科技'));

    expect(mocks.setLocation).toHaveBeenCalledWith(
      '/liquidation?symbol=000001.SZ'
    );
  });
});

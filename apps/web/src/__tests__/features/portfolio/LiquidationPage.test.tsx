import { render, screen } from '@testing-library/react';
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
  StudioWorkbench: ({ content }: { content: ReactNode }) => <>{content}</>,
}));

vi.mock('@/components/studio-workspace', () => ({
  useStudioNavigate: () => mocks.navigate,
}));

vi.mock('@/features/trading/components/TradingHoldingsSidebar', () => ({
  TradingHoldingsSidebar: () => null,
}));

vi.mock('@/features/portfolio/components/SellManagementPanels', () => ({
  ExitPlansPanel: () => <div>全部计划内容</div>,
  PositionLiquidationPanel: () => <div>持仓清仓内容</div>,
  SellHistoryPanel: () => <div>卖出记录内容</div>,
}));

vi.mock('@/features/portfolio/components/TakeProfitPlanPanel', () => ({
  TakeProfitPlanPanel: () => null,
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
    currentHoldings: [],
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
  });

  it('returns from a stock workspace to all exit plans', async () => {
    const user = userEvent.setup();
    render(<LiquidationPage />);

    await user.click(screen.getByRole('button', { name: '返回全部卖出计划' }));

    expect(mocks.setLocation).toHaveBeenCalledWith('/liquidation');
    expect(screen.getByText('全部计划内容')).toBeVisible();
  });
});

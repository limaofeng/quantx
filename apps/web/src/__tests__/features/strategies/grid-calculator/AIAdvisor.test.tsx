import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import {
  StudioWorkspaceContext,
  type StudioWorkspaceContextValue,
} from '@/components/studio-workspace';
import AIAdvisor from '@/features/strategies/components/grid-calculator/components/AIAdvisor';
import { buildGridAuditPrompt } from '@/features/strategies/components/grid-calculator/services/gridAuditPrompt';
import {
  GridType,
  type GridConfig,
  type GridResult,
} from '@/features/strategies/components/grid-calculator/types';

const config: GridConfig = {
  avgCost: 9.8,
  basePrice: 10,
  buyBudgetPct: 35,
  cashTotal: 100_000,
  coreShares: 2_000,
  gridType: GridType.GEOMETRIC,
  isStepUnified: false,
  lockedCoreShares: 1_000,
  maxPositionValuePct: 70,
  minTradeValue: 2_000,
  nDown: 3,
  nUp: 2,
  positionShares: 4_000,
  stepPctDown: 2.5,
  stepPctUp: 2,
  swingShares: 1_000,
  symbol: '600000.SH',
};

const result: GridResult = {
  basePrice: 10,
  errors: [],
  guards: {
    buyBudget: 35_000,
    maxPositionValue: 70_000,
    totalInvested: 40_000,
  },
  isValid: true,
  levels: [
    {
      amount: 5_000,
      expectedProfit: 100,
      id: 'buy-1',
      levelIndex: -1,
      pctFromBase: -2.5,
      price: 9.75,
      role: 'BUY_SLOT',
      shares: 500,
      side: 'BUY',
    },
    {
      amount: 5_000,
      expectedProfit: 100,
      id: 'sell-1',
      levelIndex: 1,
      pctFromBase: 2,
      price: 10.2,
      role: 'SELL_WATERLINE',
      shares: 500,
      side: 'SELL',
    },
  ],
};

function workspaceValue(
  openAssistant: StudioWorkspaceContextValue['openAssistant']
): StudioWorkspaceContextValue {
  return {
    activeTabId: 'page:/strategies/run',
    clearWorkspaceSidebar: vi.fn(),
    isWorkspaceHosted: true,
    openAssistant,
    openStudioTab: vi.fn(),
    setWorkspaceSidebar: vi.fn(),
    updateActiveTab: vi.fn(),
  };
}

describe('AIAdvisor', () => {
  it('opens the server AI Runtime assistant with the current grid snapshot', async () => {
    const user = userEvent.setup();
    const openAssistant = vi.fn();

    render(
      <StudioWorkspaceContext.Provider value={workspaceValue(openAssistant)}>
        <AIAdvisor config={config} result={result} />
      </StudioWorkspaceContext.Provider>
    );

    await user.click(screen.getByRole('button', { name: '在 AI 助手中审计' }));

    expect(openAssistant).toHaveBeenCalledTimes(1);
    expect(openAssistant).toHaveBeenCalledWith(
      buildGridAuditPrompt(config, result)
    );
    const prompt = String(openAssistant.mock.calls[0][0]);
    expect(prompt).toContain('标的：600000.SH');
    expect(prompt).toContain('上行步长：2.00%');
    expect(prompt).toContain('下行步长：2.50%');
    expect(prompt).toContain('计划买入预算：35000.00');
    expect(prompt).toContain('不要创建任务、修改策略或执行交易');
  });
});

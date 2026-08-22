import { render } from '@testing-library/react';

import { buildStudioWorkspaceTab } from '@/components/studio-workspace';
import {
  BuyManagementIcon,
  ControlSettingsIcon,
  LimitUpBoardIcon,
  MarketDataIcon,
  MarketResearchIcon,
  MarketWorkbenchIcon,
  PortfolioHoldingsIcon,
  SellManagementIcon,
  StockScreeningIcon,
  StrategyManagementIcon,
  TTradeCycleIcon,
} from '@/components/studio-workspace/StudioNavigationIcons';
import { getStudioNavigation } from '@/router';

const expectedRouteIcons = [
  ['/', MarketWorkbenchIcon],
  ['/holdings', PortfolioHoldingsIcon],
  ['/entry-plans', BuyManagementIcon],
  ['/t-trade', TTradeCycleIcon],
  ['/limit-up-board', LimitUpBoardIcon],
  ['/liquidation', SellManagementIcon],
  ['/strategies', StrategyManagementIcon],
  ['/research', MarketResearchIcon],
  ['/screening', StockScreeningIcon],
  ['/settings', ControlSettingsIcon],
] as const;

describe('Studio navigation icons', () => {
  it('maps every function route to its trading-specific SVG icon', () => {
    const navigationIcons = new Map(
      getStudioNavigation().flatMap(group =>
        group.items.map(item => [item.href, item.icon] as const)
      )
    );

    expectedRouteIcons.forEach(([path, Icon]) => {
      expect(navigationIcons.get(path)).toBe(Icon);
    });
    expect(new Set(expectedRouteIcons.map(([, Icon]) => Icon)).size).toBe(
      expectedRouteIcons.length
    );
  });

  it('reuses the function icons for tabs without route navigation metadata', () => {
    expect(buildStudioWorkspaceTab('/settings/data').icon).toBe(MarketDataIcon);
    expect(buildStudioWorkspaceTab('/strategies/example').icon).toBe(
      StrategyManagementIcon
    );
    expect(buildStudioWorkspaceTab('/research/study/v1/runs/run-1').icon).toBe(
      MarketResearchIcon
    );
  });

  it('keeps navigation icons compatible with the shell SVG contract', () => {
    const { container } = render(
      <BuyManagementIcon
        aria-label="买入管理图标"
        size={20}
        strokeWidth={1.75}
      />
    );
    const icon = container.querySelector('svg');

    expect(icon).toHaveAttribute('viewBox', '0 0 24 24');
    expect(icon).toHaveAttribute('width', '20');
    expect(icon).toHaveAttribute('height', '20');
    expect(icon).toHaveAttribute('stroke-width', '1.75');
    expect(icon).toHaveClass('lucide-clipboard-plus');
    expect(icon?.childElementCount).toBeGreaterThan(1);
  });
});

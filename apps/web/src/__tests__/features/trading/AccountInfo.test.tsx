import { render, screen } from '@testing-library/react';

import { AccountInfo } from '@/features/trading/components/AccountInfo';

describe('AccountInfo financial colors', () => {
  it('renders a holding loss in light blue', () => {
    render(
      <AccountInfo
        summary={{
          cash: 10000,
          frozenCash: 0,
          marketValue: 9000,
          profitLossPercent: -10,
          totalAsset: 19000,
          totalProfitLoss: -1000,
        }}
      />
    );

    expect(screen.getByText(/-¥1,000\.00/)).toHaveClass('text-holding-down');
  });

  it('renders a holding profit in red', () => {
    render(
      <AccountInfo
        summary={{
          cash: 10000,
          frozenCash: 0,
          marketValue: 11000,
          profitLossPercent: 10,
          totalAsset: 21000,
          totalProfitLoss: 1000,
        }}
      />
    );

    expect(screen.getByText(/¥1,000\.00/)).toHaveClass('text-market-up');
  });
});

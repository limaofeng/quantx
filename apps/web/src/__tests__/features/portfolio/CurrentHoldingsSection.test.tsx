import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { CurrentHoldingsSection } from '@/features/portfolio/components/CurrentHoldingsSection';
import type { Position } from '@/features/portfolio/types';

const holdings = [
  {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 10,
    canUseVolume: 150,
    createdAt: '2026-06-04T09:30:00+08:00',
    direction: 1,
    frozenVolume: 0,
    id: 'holding-1',
    instrumentName: '测试股票A',
    lastPrice: 11,
    marketValue: 1650,
    onRoadVolume: 0,
    openPrice: 10,
    profitLoss: 150,
    profitRate: 10,
    stockCode: '600519.SH',
    updatedAt: '2026-06-04T09:30:00+08:00',
    volume: 150,
    yesterdayVolume: 150,
  },
  {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 8,
    canUseVolume: 0,
    createdAt: '2026-06-04T09:30:00+08:00',
    direction: 1,
    frozenVolume: 100,
    id: 'holding-2',
    instrumentName: '冻结股票B',
    lastPrice: 8.5,
    marketValue: 850,
    onRoadVolume: 0,
    openPrice: 8,
    profitLoss: 50,
    profitRate: 6.25,
    stockCode: '000001.SZ',
    updatedAt: '2026-06-04T09:30:00+08:00',
    volume: 100,
    yesterdayVolume: 100,
  },
] as Position[];

function Harness({
  onSubmit,
}: {
  onSubmit: (stockCodes: string[]) => Promise<unknown>;
}) {
  const [selectedHoldings, setSelectedHoldings] = useState<string[]>([]);

  return (
    <CurrentHoldingsSection
      holdings={holdings}
      liquidateMultiple={onSubmit}
      onLiquidateSelected={() => onSubmit(selectedHoldings)}
      onSelectionChange={setSelectedHoldings}
      selectedHoldings={selectedHoldings}
    />
  );
}

describe('CurrentHoldingsSection', () => {
  it('submits selected stock codes instead of holding ids', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(<Harness onSubmit={onSubmit} />);

    await user.click(screen.getByTestId('checkbox-600519.SH'));
    await user.click(screen.getByTestId('liquidate-selected-button'));
    await user.click(
      await screen.findByRole('button', { name: '提交清仓委托' })
    );

    expect(onSubmit).toHaveBeenCalledWith(['600519.SH']);
    expect(onSubmit).not.toHaveBeenCalledWith(['holding-1']);
  });

  it('disables holdings without sellable volume', () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(<Harness onSubmit={onSubmit} />);

    expect(screen.getByTestId('checkbox-000001.SZ')).toBeDisabled();
    expect(screen.getByTestId('liquidate-000001.SZ')).toBeDisabled();
  });
});

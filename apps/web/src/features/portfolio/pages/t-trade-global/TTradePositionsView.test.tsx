import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  TTradePositionsView,
  type TTradePositionBatch,
} from './TTradePositionsView';

const baseBatch: TTradePositionBatch = {
  batchId: 'batch-1',
  stockCode: '600519.SH',
  strategyRunId: 'run-1',
  status: 'COMPLETED',
  entryClientOrderId: 'entry-order-1',
  exitClientOrderId: 'exit-order-1',
  targetVolume: 100,
  entryFilledVolume: 100,
  entryAvgPrice: 1500,
  exitFilledVolume: 100,
  exitAvgPrice: 1504,
  activeVolume: 0,
  lastPrice: 1504,
  netProfit: 380,
  lastNetProfitPct: 0.25,
  peakNetProfitPct: 0.25,
  trailingFloorPct: null,
  exitReason: 'trailing_take_profit',
};

describe('TTradePositionsView', () => {
  it('renders isolated replay batches without live cancellation actions', () => {
    render(
      <TTradePositionsView
        batches={[baseBatch]}
        instrumentNames={new Map([['600519.SH', '贵州茅台']])}
        loading={false}
        mode="REPLAY"
        onCancelOrder={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    expect(screen.getByText(/不读取当前实盘账户/)).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getAllByText('BACKTEST_BROKER')).toHaveLength(1);
    expect(screen.getByText('+¥380.00')).toBeInTheDocument();
    expect(screen.getByText('收益率 +0.25%')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '申请撤单' })
    ).not.toBeInTheDocument();
  });

  it('keeps live cancellation available for cancellable batches', () => {
    const onCancelOrder = vi.fn();
    render(
      <TTradePositionsView
        batches={[
          {
            ...baseBatch,
            status: 'ENTRY_SUBMITTED',
            activeVolume: 100,
            exitFilledVolume: 0,
            exitAvgPrice: 0,
          },
        ]}
        instrumentNames={new Map()}
        loading={false}
        mode="LIVE"
        onCancelOrder={onCancelOrder}
        onRefresh={vi.fn()}
      />
    );

    screen.getByRole('button', { name: '申请撤单' }).click();
    expect(onCancelOrder).toHaveBeenCalledWith('entry-order-1');
  });
});

import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { isCurrentTTradeBatch } from './batchWorkspace';
import {
  TTradePositionsView,
  type TTradePositionBatch,
} from './TTradePositionsView';

const baseBatch: TTradePositionBatch = {
  batchId: 'batch-1-complete',
  stockCode: '600519.SH',
  strategyRunId: 'run-1',
  status: 'CLOSED',
  executionMode: 'BACKTEST',
  entryClientOrderId: 'entry-order-1',
  exitClientOrderId: 'exit-order-1',
  targetVolume: 100,
  entryFilledVolume: 100,
  entryAvgPrice: 1500,
  exitFilledVolume: 100,
  exitAvgPrice: 1504,
  activeVolume: 0,
  lastPrice: 1504,
  priceAsOf: '2026-08-27T14:30:00+08:00',
  priceQuality: 'FRESH',
  netProfit: 380,
  lastNetProfitPct: 0.25,
  peakNetProfitPct: 0.25,
  trailingFloorPct: null,
  exitReason: 'trailing_take_profit',
  createdAt: '2026-08-27T10:00:00+08:00',
  entryFilledAt: '2026-08-27T10:01:00+08:00',
  terminalAt: '2026-08-27T14:30:00+08:00',
  closedAt: '2026-08-27T14:30:00+08:00',
  metrics: {
    metricBasis: 'BACKTEST_MODEL',
    entryCapitalCny: 150000,
    totalFeesCny: 20,
    realizedNetProfitCny: 380,
    markToMarketNetProfitCny: 380,
    netReturnPct: 0.25,
    holdingHours: 4.5,
    capitalUtilizationPct: 88,
  },
};

function renderView(
  overrides: Partial<ComponentProps<typeof TTradePositionsView>> = {}
) {
  const props: ComponentProps<typeof TTradePositionsView> = {
    batches: [baseBatch],
    instrumentNames: new Map([['600519.SH', '贵州茅台']]),
    loading: false,
    mode: 'REPLAY',
    onRefresh: vi.fn(),
    ...overrides,
  };
  return { ...render(<TTradePositionsView {...props} />), props };
}

describe('TTradePositionsView', () => {
  it('normalizes the replay workspace into end positions and historical batches', () => {
    renderView();

    expect(screen.getByText(/不读取当前实盘账户/)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /期末仓位/ })).toHaveAttribute(
      'aria-selected',
      'true'
    );

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getAllByText('回测模型').length).toBeGreaterThan(0);
    expect(screen.queryByText('估算')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /贵州茅台/ }));
    expect(
      screen.getByRole('heading', { name: /600519.SH · 批次详情/ })
    ).toBeInTheDocument();
    expect(screen.getAllByText('+¥380.00').length).toBeGreaterThan(0);
    expect(screen.getByText('BACKTEST_BROKER')).toBeInTheDocument();
  });

  it('keeps cancellation in the current view and hides an untrusted live price', () => {
    const onCancelOrder = vi.fn();
    renderView({
      batches: [
        {
          ...baseBatch,
          status: 'ENTRY_SUBMITTED',
          executionMode: 'LIVE',
          activeVolume: 100,
          exitFilledVolume: 0,
          exitAvgPrice: 0,
          priceAsOf: null,
          priceQuality: 'MISSING',
          metrics: {
            ...baseBatch.metrics!,
            metricBasis: 'RULE_ESTIMATE',
            markToMarketNetProfitCny: 120,
          },
        },
      ],
      mode: 'LIVE',
      onCancelOrder,
    });

    expect(screen.getAllByText('暂无可信行情').length).toBeGreaterThan(0);
    screen.getByRole('button', { name: '申请撤单' }).click();
    expect(onCancelOrder).toHaveBeenCalledWith('entry-order-1');
  });

  it('classifies by remaining volume before terminal status', () => {
    const closedWithRemainder = {
      ...baseBatch,
      batchId: 'closed-with-remainder',
      status: 'CLOSED',
      activeVolume: 100,
    };
    const rejectedExit = {
      ...baseBatch,
      batchId: 'exit-rejected-current',
      status: 'EXIT_REJECTED',
      activeVolume: 0,
    };
    const killSwitched = {
      ...baseBatch,
      batchId: 'kill-switched-current',
      status: 'KILL_SWITCHED',
      activeVolume: 0,
    };
    const entryRejected = {
      ...baseBatch,
      batchId: 'entry-rejected-history',
      status: 'ENTRY_REJECTED',
      activeVolume: 0,
    };

    expect(isCurrentTTradeBatch(closedWithRemainder)).toBe(true);
    expect(isCurrentTTradeBatch(rejectedExit)).toBe(true);
    expect(isCurrentTTradeBatch(killSwitched)).toBe(true);
    expect(isCurrentTTradeBatch(baseBatch)).toBe(false);
    expect(isCurrentTTradeBatch(entryRejected)).toBe(false);
  });

  it('opens a focus-trapped detail drawer with real events and batch navigation', () => {
    const onInspectBatch = vi.fn();
    const onViewActivity = vi.fn();
    renderView({
      events: [
        {
          eventId: 'event-1',
          batchId: baseBatch.batchId,
          eventType: 'TRADE',
          status: 'APPLIED',
          clientOrderId: 'entry-order-1',
          brokerOrderId: 'broker-order-1',
          createdAt: '2026-08-27T10:01:00+08:00',
        },
      ],
      onInspectBatch,
      onViewActivity,
    });

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    fireEvent.click(screen.getByRole('button', { name: /贵州茅台/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('真实生命周期事件')).toBeInTheDocument();
    expect(within(dialog).getByText('broker-order-1')).toBeInTheDocument();
    expect(onInspectBatch).toHaveBeenCalledWith(baseBatch.batchId);

    fireEvent.click(
      within(dialog).getByRole('button', { name: /在运行动态中查看/ })
    );
    expect(onViewActivity).toHaveBeenCalledWith(baseBatch.batchId);
  });

  it('filters history by keyword while keeping compact summary coverage truthful', () => {
    renderView({
      batches: [
        baseBatch,
        {
          ...baseBatch,
          batchId: 'batch-2-legacy',
          stockCode: '688213.SH',
          executionMode: 'LIVE',
          metrics: {
            ...baseBatch.metrics!,
            metricBasis: 'LEGACY_BACKFILL',
            realizedNetProfitCny: -50,
          },
        },
      ],
      mode: 'LIVE',
    });

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    fireEvent.change(
      screen.getByPlaceholderText('标的、批次、运行或委托编号'),
      { target: { value: '688213' } }
    );
    expect(screen.getByText('688213.SH')).toBeInTheDocument();
    expect(screen.queryByText('贵州茅台')).not.toBeInTheDocument();
    expect(screen.getAllByText('历史回填').length).toBeGreaterThan(0);
    expect(screen.getByText('1/1 批指标完整')).toBeInTheDocument();
  });

  it('recomputes history KPIs from the rows selected by the date filter', () => {
    renderView({
      batches: [
        baseBatch,
        {
          ...baseBatch,
          batchId: 'batch-older-loss',
          stockCode: '688213.SH',
          entryFilledAt: '2026-08-25T10:00:00+08:00',
          terminalAt: '2026-08-25T14:00:00+08:00',
          closedAt: '2026-08-25T14:00:00+08:00',
          metrics: {
            ...baseBatch.metrics!,
            realizedNetProfitCny: -50,
          },
        },
      ],
      summary: {
        total: 99,
        completed: 99,
        completionRate: 100,
        winning: 99,
        winRate: 100,
        feesCny: 999,
        netProfitCny: 9_999,
        averageHoldingHours: 99,
        capitalUtilizationPct: 99,
        coverage: 99,
      },
    });

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    const netProfitKpi = screen.getByText('税费后模型净增量').parentElement!;
    expect(within(netProfitKpi).getByText('+¥330.00')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('历史开始日期'), {
      target: { value: '2026-08-27' },
    });
    expect(within(netProfitKpi).getByText('+¥380.00')).toBeInTheDocument();
    expect(screen.queryByText('688213.SH')).not.toBeInTheDocument();
  });

  it('states partial and missing metric coverage without presenting missing totals as zero', () => {
    const incompleteBatch: TTradePositionBatch = {
      ...baseBatch,
      batchId: 'batch-incomplete',
      stockCode: '688213.SH',
      metrics: {
        metricBasis: 'INCOMPLETE',
        entryCapitalCny: null,
        totalFeesCny: null,
        realizedNetProfitCny: null,
        markToMarketNetProfitCny: null,
        netReturnPct: null,
        holdingHours: null,
        capitalUtilizationPct: null,
      },
    };
    const view = renderView({
      batches: [baseBatch, incompleteBatch],
    });

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    expect(
      screen.getByText('仅合计已覆盖批次 · 1/2 批指标完整')
    ).toBeInTheDocument();

    view.rerender(
      <TTradePositionsView {...view.props} batches={[incompleteBatch]} />
    );
    expect(
      screen.getByText('0/1 批指标完整 · 收益与税费暂无可比口径')
    ).toBeInTheDocument();
    const netProfitKpi = screen.getByText('税费后模型净增量').parentElement!;
    expect(within(netProfitKpi).getByText('--')).toBeInTheDocument();
  });

  it('resets replay dates when the replay dataset changes', () => {
    const view = renderView({ historyScopeKey: 'run-1' });
    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    fireEvent.change(screen.getByLabelText('历史开始日期'), {
      target: { value: '2026-08-27' },
    });
    fireEvent.change(screen.getByLabelText('历史结束日期'), {
      target: { value: '2026-08-27' },
    });

    const nextBatch: TTradePositionBatch = {
      ...baseBatch,
      batchId: 'batch-run-2',
      stockCode: '688213.SH',
      strategyRunId: 'run-2',
      entryFilledAt: '2026-09-15T10:00:00+08:00',
      terminalAt: '2026-09-15T14:00:00+08:00',
      closedAt: '2026-09-15T14:00:00+08:00',
    };
    view.rerender(
      <TTradePositionsView
        {...view.props}
        batches={[nextBatch]}
        historyScopeKey="run-2"
      />
    );

    expect(screen.getByLabelText('历史开始日期')).toHaveValue('2026-09-15');
    expect(screen.getByLabelText('历史结束日期')).toHaveValue('2026-09-15');
    expect(screen.getByText('688213.SH')).toBeInTheDocument();
  });

  it('keeps live history dates inside the loaded rolling 30-day window', () => {
    renderView({ mode: 'LIVE', historyScopeKey: 'account-1' });
    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));

    const startInput = screen.getByLabelText('历史开始日期');
    const endInput = screen.getByLabelText('历史结束日期');
    const rangeStart = startInput.getAttribute('min');
    const rangeEnd = endInput.getAttribute('max');

    expect(rangeStart).toBe(startInput.getAttribute('value'));
    expect(rangeEnd).toBe(endInput.getAttribute('value'));
    expect(startInput).toHaveAttribute('max', rangeEnd);
    expect(endInput).toHaveAttribute('min', rangeStart);

    fireEvent.change(startInput, { target: { value: '2000-01-01' } });
    fireEvent.change(endInput, { target: { value: '2100-01-01' } });
    expect(startInput).toHaveValue(rangeStart);
    expect(endInput).toHaveValue(rangeEnd);
  });

  it('extends an untouched replay range for a second page but preserves a manual range', () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => ({
      ...baseBatch,
      batchId: `batch-page-1-${index}`,
      entryFilledAt: '2026-08-27T10:00:00+08:00',
      terminalAt: '2026-08-27T14:00:00+08:00',
      closedAt: '2026-08-27T14:00:00+08:00',
    }));
    const secondPageBatch: TTradePositionBatch = {
      ...baseBatch,
      batchId: 'batch-page-2-older',
      entryFilledAt: '2026-08-01T10:00:00+08:00',
      terminalAt: '2026-08-01T14:00:00+08:00',
      closedAt: '2026-08-01T14:00:00+08:00',
    };
    const view = renderView({
      batches: firstPage,
      historyScopeKey: 'run-1',
    });
    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    expect(screen.getByLabelText('历史开始日期')).toHaveValue('2026-08-27');

    view.rerender(
      <TTradePositionsView
        {...view.props}
        batches={[...firstPage, secondPageBatch]}
        historyScopeKey="run-1"
      />
    );
    expect(screen.getByLabelText('历史开始日期')).toHaveValue('2026-08-01');

    fireEvent.change(screen.getByLabelText('历史开始日期'), {
      target: { value: '2026-08-20' },
    });
    view.rerender(
      <TTradePositionsView
        {...view.props}
        batches={[
          ...firstPage,
          secondPageBatch,
          {
            ...secondPageBatch,
            batchId: 'batch-page-2-oldest',
            entryFilledAt: '2026-07-01T10:00:00+08:00',
            terminalAt: '2026-07-01T14:00:00+08:00',
            closedAt: '2026-07-01T14:00:00+08:00',
          },
        ]}
        historyScopeKey="run-1"
      />
    );
    expect(screen.getByLabelText('历史开始日期')).toHaveValue('2026-08-20');
  });

  it('uses terminal time for rejected history without labeling it as a fill close', () => {
    renderView({
      batches: [
        {
          ...baseBatch,
          batchId: 'batch-entry-rejected',
          status: 'ENTRY_REJECTED',
          entryFilledVolume: 0,
          exitFilledVolume: 0,
          terminalAt: '2026-08-28T11:00:00+08:00',
          closedAt: null,
          updatedAt: '2026-08-29T15:00:00+08:00',
          metrics: {
            ...baseBatch.metrics!,
            metricBasis: 'INCOMPLETE',
            realizedNetProfitCny: null,
          },
        },
      ],
    });

    fireEvent.click(screen.getByRole('tab', { name: /历史批次/ }));
    fireEvent.change(screen.getByLabelText('历史开始日期'), {
      target: { value: '2026-08-28' },
    });
    fireEvent.change(screen.getByLabelText('历史结束日期'), {
      target: { value: '2026-08-28' },
    });
    fireEvent.click(screen.getByRole('button', { name: /贵州茅台/ }));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('成交关闭')).toBeInTheDocument();
    expect(within(dialog).getByText('终态时间')).toBeInTheDocument();
    expect(within(dialog).getAllByText(/08.*28.*11:00/)).not.toHaveLength(0);
  });
});

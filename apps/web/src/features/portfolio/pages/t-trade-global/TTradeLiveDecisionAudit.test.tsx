import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type {
  ExecutionTraceView,
  StrategyDecision,
} from '@/features/strategies/domain';

import { TTradeLiveDecisionAudit } from './TTradeLiveDecisionAudit';

const noIntentDecision: StrategyDecision = {
  id: 'decision-no-intent',
  instanceId: 'run-live',
  decidedAt: '2026-09-01T01:30:00Z',
  inputSummary: { instrument_code: '600000.SH', cadence: 'TICK' },
  outputSummary: { trade_intent_count: 0 },
  tradeIntents: [],
  statePatch: {},
  decisionTrace: ['MINIMUM_COVERAGE_NOT_REACHED'],
};

const intentDecision: StrategyDecision = {
  id: 'decision-intent',
  instanceId: 'run-live',
  decidedAt: '2026-09-01T01:31:00Z',
  inputSummary: { instrument_code: '600000.SH', cadence: 'TICK' },
  outputSummary: { trade_intent_count: 1 },
  tradeIntents: [
    {
      id: 'intent-live',
      side: 'BUY',
      instrumentCode: '600000.SH',
      quantityIntent: 100,
      reason: 't_trade_opportunity_candidate',
      status: 'SUBMITTED',
    },
  ],
  statePatch: {},
  decisionTrace: ['T_TRADE_OPPORTUNITY_CANDIDATE_LATCHED'],
};

const execution: ExecutionTraceView = {
  id: 'execution-live',
  intentId: 'intent-live',
  instrumentCode: '600000.SH',
  side: 'BUY',
  orderId: 'broker-order-1',
  riskDecision: 'ALLOW',
  sizingResult: '100 股',
  orderStatus: 'FILLED',
  fillStatus: 'FILLED',
  executedPrice: 10.25,
  executedVolume: 100,
  executedTime: '2026-09-01T01:31:02Z',
  reason: null,
  traceId: 'trace-live',
};

describe('TTradeLiveDecisionAudit', () => {
  it('uses the shared audit presentation for live decisions and broker facts', () => {
    render(
      <TTradeLiveDecisionAudit
        decisions={[intentDecision, noIntentDecision]}
        executions={[execution]}
        instrumentNames={new Map([['600000.SH', '浦发银行']])}
        loading={false}
        onRefresh={vi.fn()}
        runId="run-live"
      />
    );

    expect(
      screen.getByRole('region', { name: '实盘决策审计' })
    ).toBeInTheDocument();
    expect(screen.getByText('产生意图 · 1')).toBeInTheDocument();
    expect(screen.getAllByText('未发意图').length).toBeGreaterThan(1);
    expect(screen.getAllByText('已成交').length).toBeGreaterThan(1);

    fireEvent.click(
      screen.getAllByRole('button', { name: /决策 600000.SH/ })[0]
    );
    expect(screen.getByText('交易意图与实盘执行')).toBeInTheDocument();
    expect(screen.getAllByText(/券商成交回报/).length).toBeGreaterThan(1);
    expect(screen.getByText('真实成交')).toBeInTheDocument();
    expect(screen.queryByText('模拟成交')).not.toBeInTheDocument();
  });

  it('keeps material no-intent decisions searchable and refreshable', () => {
    const onRefresh = vi.fn();
    render(
      <TTradeLiveDecisionAudit
        decisions={[noIntentDecision]}
        error="暂时不可用"
        executions={[]}
        instrumentNames={new Map([['600000.SH', '浦发银行']])}
        loading={false}
        onRefresh={onRefresh}
        runId="run-live"
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('仍显示上次成功读取');
    fireEvent.change(
      screen.getByRole('textbox', { name: '搜索实盘决策审计' }),
      {
        target: { value: '覆盖不足' },
      }
    );
    expect(
      screen.getByRole('button', { name: /决策 600000.SH/ })
    ).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole('textbox', { name: '搜索实盘决策审计' }),
      {
        target: { value: '不存在的决策' },
      }
    );
    expect(screen.getByText('当前筛选没有匹配记录')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});

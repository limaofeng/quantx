import { render, screen } from '@testing-library/react';
import { CombinedError } from 'urql';
import { describe, expect, it, vi } from 'vitest';

import { ManualOrderAttemptRecords } from '@/features/trading/components';
import type {
  ManualOrderAttemptItem,
  ManualOrderAttemptsState,
} from '@/features/trading/hooks';
import {
  ManualOrderAttemptPhase,
  ManualOrderExecutionMode,
  ManualOrderSide,
} from '@/generated/gql/graphql';

function makeAttempt(
  overrides: Partial<ManualOrderAttemptItem> = {}
): ManualOrderAttemptItem {
  return {
    __typename: 'ManualOrderAttempt',
    accountId: '300000013250',
    acknowledgedAt: null,
    active: true,
    brokerOrderId: null,
    clientOrderId: 'client-order-1',
    createdAt: '2026-09-01T14:00:00.000Z',
    deliveredAt: null,
    deliveryStatus: 'QUEUED',
    executionMode: ManualOrderExecutionMode.Paper,
    expiresAt: '2026-09-01T14:02:00.000Z',
    instrumentCode: '688577.SH',
    limitPrice: '48.76',
    message: '下单请求已进入可靠队列，尚未生成券商委托',
    orderType: 'FIX_PRICE',
    phase: ManualOrderAttemptPhase.Queued,
    requiresAttention: false,
    side: ManualOrderSide.Sell,
    status: 'QUEUED',
    statusReason: null,
    updatedAt: '2026-09-01T14:00:00.000Z',
    volume: 420,
    ...overrides,
  };
}

function makeState(
  items: ManualOrderAttemptItem[],
  overrides: Partial<ManualOrderAttemptsState> = {}
): ManualOrderAttemptsState {
  return {
    activeCount: items.filter(item => item.active).length,
    asOf: '2026-09-01T14:00:00.000Z',
    error: undefined,
    feed: null,
    hasSuccessfulData: true,
    isRefreshing: false,
    items,
    lastUpdatedAt: '2026-09-01T14:00:00.000Z',
    loading: false,
    refresh: vi.fn(),
    requiresAttentionCount: items.filter(item => item.requiresAttention).length,
    totalCount: items.length,
    truncated: false,
    ...overrides,
  };
}

describe('ManualOrderAttemptRecords', () => {
  it('shows a durable queued request without claiming broker acceptance', () => {
    render(<ManualOrderAttemptRecords state={makeState([makeAttempt()])} />);

    expect(screen.getByText('下单请求')).toBeVisible();
    expect(screen.getByText('已排队，等待下发')).toBeVisible();
    expect(screen.getByText('尚无券商委托')).toBeVisible();
    expect(screen.queryByText(/券商委托已生成/)).toBeNull();
  });

  it('keeps an uncertain association visible without offering a guessed jump', () => {
    render(
      <ManualOrderAttemptRecords
        state={makeState([
          makeAttempt({
            brokerOrderId: 'broker-123',
            message: '结果待核对，禁止重复下单；请先核对券商端',
            phase: ManualOrderAttemptPhase.ReconcileRequired,
            requiresAttention: true,
          }),
        ])}
      />
    );

    expect(screen.getByText('结果待核对，禁止重复下单')).toBeVisible();
    expect(screen.getByText('已有关联，需核对')).toBeVisible();
    expect(screen.queryByRole('button', { name: '查看券商委托' })).toBeNull();
  });

  it('distinguishes an initial error from an empty successful response', () => {
    const refresh = vi.fn();
    const error = new CombinedError({
      networkError: new Error('会话已过期'),
    });
    const { rerender } = render(
      <ManualOrderAttemptRecords
        state={makeState([], {
          error,
          hasSuccessfulData: false,
          refresh,
        })}
      />
    );

    expect(screen.getByText('下单请求列表加载失败')).toBeVisible();
    expect(screen.getByText('[Network] 会话已过期')).toBeVisible();
    expect(screen.getByRole('button', { name: '重新加载' })).toBeVisible();

    rerender(<ManualOrderAttemptRecords state={makeState([])} />);
    expect(screen.getByText('暂无下单请求')).toBeVisible();
    expect(screen.queryByText('暂无委托记录')).toBeNull();
  });
});

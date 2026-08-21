import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ManualPlanEditor } from '@/features/portfolio/components/SellManagementPanels';
import {
  ConfirmExitPlanAuthorizationMutation,
  CreateManualExitPlanMutation,
  PreviewExitPlanAuthorizationMutation,
  UpdateManualExitPlanMutation,
} from '@/features/portfolio/hooks/usePortfolio';

const mocks = vi.hoisted(() => ({
  confirmAuthorization: vi.fn(),
  createPlan: vi.fn(),
  mutate: vi.fn(),
  previewAuthorization: vi.fn(),
  refetch: vi.fn(),
  toast: vi.fn(),
  updatePlan: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

const capabilitiesData = {
  exitPlanCapabilities: {
    completionStrategies: ['AVAILABLE_NOW'],
    conflictStrategies: ['UNALLOCATED_ONLY'],
    executionModes: ['paper', 'live'],
    ruleSemantics: 'OR；按 priority 从高到低决定首个执行规则',
    ruleTypes: [
      {
        category: 'price',
        label: '目标价',
        parameters: { target_price: 'number' },
        ruleType: 'TARGET_PRICE',
      },
      {
        category: 'trailing',
        label: '量价动态止盈',
        parameters: {},
        ruleType: 'ADAPTIVE_VOLUME_PRICE_TRAILING',
      },
      {
        category: 'risk',
        label: '硬止损',
        parameters: { stop_loss_pct: 'number' },
        ruleType: 'HARD_STOP',
      },
    ],
  },
};

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
  useQuery: mocks.useQuery,
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

describe('ManualPlanEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQuery.mockReturnValue([
      { data: undefined, error: undefined, fetching: false },
      mocks.refetch,
    ]);
    mocks.useMutation.mockImplementation(document => {
      const mutation =
        document === CreateManualExitPlanMutation
          ? mocks.createPlan
          : document === UpdateManualExitPlanMutation
            ? mocks.updatePlan
            : document === PreviewExitPlanAuthorizationMutation
              ? mocks.previewAuthorization
              : document === ConfirmExitPlanAuthorizationMutation
                ? mocks.confirmAuthorization
                : mocks.mutate;
      return [{ data: undefined, error: undefined, fetching: false }, mutation];
    });
  });

  it('uses the full row when expanded', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="300917.SZ"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));

    expect(container.querySelector('section')).toHaveClass(
      'w-full',
      'basis-full'
    );
  });

  it('keeps the first-row controls aligned when helper text is present', async () => {
    const user = userEvent.setup();
    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="601318.SH"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));

    expect(screen.getByLabelText('股票').closest('label')).toHaveClass(
      'content-start'
    );
    expect(screen.getByLabelText('计划卖出数量').parentElement).toHaveClass(
      'content-start'
    );
    expect(screen.getByLabelText('模式').closest('label')).toHaveClass(
      'content-start'
    );
    await user.selectOptions(screen.getByLabelText('模式'), 'live');
    const liveAuthorization =
      screen.getByLabelText('保存后预览并授权自动实盘卖出');
    expect(liveAuthorization.closest('label')).toHaveClass(
      'h-9',
      'items-center'
    );
    expect(liveAuthorization.closest('div')).toHaveClass('content-start');
    expect(liveAuthorization.closest('div')).not.toHaveClass('md:mt-5');
  });

  it('synchronizes a new-plan draft with the selected holding', async () => {
    const user = userEvent.setup();
    const props = {
      accountId: '300000013250',
      onFinishedEditing: vi.fn(),
      onSaved: vi.fn(),
    };
    const { rerender } = render(
      <ManualPlanEditor {...props} initialInstrumentCode="300917.SZ" />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));
    expect(screen.getByLabelText('股票')).toHaveValue('300917.SZ');

    rerender(<ManualPlanEditor {...props} initialInstrumentCode="302132.SZ" />);

    expect(screen.getByLabelText('股票')).toHaveValue('302132.SZ');
  });

  it('explains the planned sell quantity in user-facing terms', async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockReturnValue([
      {
        data: {
          ...capabilitiesData,
          exitPlanHoldingCapacity: {
            protectedVolume: 0,
            totalVolume: 1100,
            unallocatedVolume: 1100,
          },
        },
        error: undefined,
        fetching: false,
      },
      mocks.refetch,
    ]);

    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="600887.SH"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));

    expect(screen.getByLabelText('计划卖出数量')).toHaveAccessibleDescription(
      '触发条件满足后，最多卖出该数量；创建计划不会立即下单。'
    );
    expect(screen.getByText(/持仓 1100/)).toHaveTextContent(
      '持仓 1100 · 已纳入计划 0 · 可加入计划 1100 股'
    );
    expect(screen.queryByText(/保护数量/)).not.toBeInTheDocument();
  });

  it('explains strategies and configures parameters without exposing a native select', async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockReturnValue([
      { data: capabilitiesData, error: undefined, fetching: false },
      mocks.refetch,
    ]);
    mocks.createPlan.mockResolvedValue({ data: {}, error: undefined });
    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="605499.SH"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));

    const strategyPicker = screen.getByRole('button', {
      name: '规则 1 类型',
    });
    expect(strategyPicker).toHaveTextContent('目标价');
    expect(
      screen.queryByRole('combobox', { name: '规则 1 类型' })
    ).not.toBeInTheDocument();

    await user.click(strategyPicker);

    expect(screen.getByText('选择卖出策略')).toBeVisible();
    expect(
      screen.getByText(/系统自动判断强弱，不需要你预判快速上涨/)
    ).toBeVisible();

    await user.click(screen.getByRole('radio', { name: /量价动态止盈/ }));

    expect(strategyPicker).toHaveTextContent('量价动态止盈');
    expect(screen.getByLabelText('开始跟踪收益率')).toHaveValue(2);
    expect(screen.getByLabelText('立即退出回撤')).toHaveValue(1.2);
    expect(screen.getByLabelText('转弱确认次数')).toHaveValue(2);
    expect(screen.getByText(/强势跟涨/)).toBeVisible();

    await user.click(screen.getByRole('button', { name: '高级设置' }));
    const advancedJson = screen.getByLabelText(
      '规则 1 参数 JSON'
    ) as HTMLTextAreaElement;
    expect(advancedJson.value).toContain('"arm_target_profit_pct":2');
    expect(advancedJson.value).not.toContain('target_price');

    await user.type(screen.getByLabelText('计划卖出数量'), '100');
    await user.click(screen.getByRole('radio', { name: /手工填写每股全成本/ }));
    await user.type(screen.getByLabelText('每股全成本（元）'), '10.25');
    await user.click(screen.getByRole('button', { name: '创建卖出计划' }));

    expect(mocks.createPlan).toHaveBeenCalledWith({
      input: expect.objectContaining({
        autoExitAuthorized: false,
        executionMode: 'paper',
        idempotencyKey: expect.any(String),
        instrumentCode: '605499.SH',
        protectedVolume: 100,
        costBasis: {
          mode: 'MANUAL_UNIT_COST',
          orderIds: undefined,
          unitCostCny: 10.25,
        },
        rules: [
          expect.objectContaining({
            parameters: expect.objectContaining({
              arm_target_profit_pct: 2,
              confirm_observations: 2,
              immediate_drawdown_pct: 1.2,
            }),
            strategy: 'ADAPTIVE_VOLUME_PRICE_TRAILING',
          }),
        ],
      }),
    });
  });

  it('uses selected completed buy orders as the frozen cost basis', async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockImplementation(options => {
      const variables = options.variables as { limit?: number } | undefined;
      if (variables?.limit === 100) {
        return [
          {
            data: {
              exitPlanCostBasisCandidates: {
                accountId: '300000013250',
                historyWarning: '仅展示 QuantX 已持久化的成交委托',
                instrumentCode: '601318.SH',
                items: [
                  {
                    estimatedBuyFeeCny: 5.12,
                    orderId: '9001',
                    orderTime: '2026-08-20T10:00:00+08:00',
                    remark: null,
                    strategyName: null,
                    tradedPrice: 12.5,
                    tradedVolume: 300,
                  },
                ],
              },
            },
            error: undefined,
            fetching: false,
          },
          mocks.refetch,
        ];
      }
      return [
        {
          data: variables ? undefined : capabilitiesData,
          error: undefined,
          fetching: false,
        },
        mocks.refetch,
      ];
    });
    mocks.createPlan.mockResolvedValue({
      data: {
        createManualExitPlan: {
          configVersion: 1,
          instrumentCode: '601318.SH',
          planId: 'plan-order-basis',
          protectedVolume: 300,
          status: 'ACTIVE',
        },
      },
      error: undefined,
    });

    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="601318.SH"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));
    await user.type(screen.getByLabelText('计划卖出数量'), '300');
    expect(
      screen.getByText(/整笔买入委托只能作为一个有效卖出计划的成本依据/)
    ).toBeVisible();
    await user.click(screen.getByRole('checkbox', { name: /委托 #9001/ }));
    await user.click(screen.getByRole('button', { name: '创建卖出计划' }));

    expect(mocks.createPlan).toHaveBeenCalledWith({
      input: expect.objectContaining({
        costBasis: {
          mode: 'BROKER_BUY_ORDERS',
          orderIds: ['9001'],
          unitCostCny: undefined,
        },
        protectedVolume: 300,
      }),
    });
  });

  it('reuses the create idempotency key when the same draft is retried', async () => {
    const user = userEvent.setup();
    mocks.createPlan
      .mockResolvedValueOnce({
        data: undefined,
        error: new Error('Engine 尚未确认操作'),
      })
      .mockResolvedValueOnce({
        data: {
          createManualExitPlan: {
            configVersion: 1,
            instrumentCode: '601318.SH',
            planId: 'plan-retried-1',
            protectedVolume: 300,
            status: 'ACTIVE',
          },
        },
        error: undefined,
      });

    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="601318.SH"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));
    await user.type(screen.getByLabelText('计划卖出数量'), '300');
    await user.click(screen.getByRole('radio', { name: /手工填写每股全成本/ }));
    await user.type(screen.getByLabelText('每股全成本（元）'), '12.5');
    await user.click(screen.getByRole('button', { name: '创建卖出计划' }));
    await user.click(screen.getByRole('button', { name: '创建卖出计划' }));

    await waitFor(() => expect(mocks.createPlan).toHaveBeenCalledTimes(2));
    const firstKey = mocks.createPlan.mock.calls[0][0].input.idempotencyKey;
    const secondKey = mocks.createPlan.mock.calls[1][0].input.idempotencyKey;
    expect(firstKey).toEqual(expect.any(String));
    expect(firstKey).not.toHaveLength(0);
    expect(secondKey).toBe(firstKey);
  });

  it('saves a live plan without boolean authorization, then previews and confirms the exact plan version', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mocks.createPlan.mockResolvedValue({
      data: {
        createManualExitPlan: {
          configVersion: 3,
          instrumentCode: '601318.SH',
          planId: 'plan-live-1',
          protectedVolume: 300,
          status: 'ACTIVE',
        },
      },
      error: undefined,
    });
    mocks.previewAuthorization.mockResolvedValue({
      data: {
        previewExitPlanAuthorization: {
          code: 'PREVIEW_READY',
          message: '请核对后确认',
          preview: {
            accountId: '300000013250',
            authorizationExpiresAt: '2026-08-28T10:00:00+08:00',
            authorizationFingerprint: 'fingerprint-live-1',
            bucket: 'manual',
            challengeExpiresAt: '2099-08-21T10:05:00+08:00',
            challengeId: 'challenge-live-1',
            configVersion: 3,
            costBasis: {
              basis_volume: 300,
              frozen_at: '2026-08-21T10:00:00+08:00',
              mode: 'MANUAL_UNIT_COST',
              unit_cost_cny: 12.5,
            },
            confirmationToken: 'confirmation-token-live-1',
            executionMode: 'live',
            executionPolicy: { order_type: 'LIMIT' },
            exitedVolume: 0,
            instrumentCode: '601318.SH',
            otherProtections: [],
            planId: 'plan-live-1',
            position: {
              availableVolume: 800,
              frozenVolume: 0,
              positionUpdatedAt: '2026-08-21T10:00:00+08:00',
              t1UnavailableVolume: 0,
              totalVolume: 800,
              yesterdayVolume: 800,
            },
            protectedVolume: 300,
            readiness: { live_trading_enabled: true },
            remainingVolume: 300,
            rules: [{ strategy: 'TARGET_PRICE' }],
            sourceType: 'MANUAL_POSITION',
            t1Policy: 'AVAILABLE_ONLY',
            warnings: ['确认不创建委托'],
          },
          success: true,
        },
      },
      error: undefined,
    });
    mocks.confirmAuthorization.mockResolvedValue({
      data: {
        confirmExitPlanAuthorization: {
          auditEventId: 'audit-live-1',
          authorizationExpiresAt: '2026-08-28T10:00:00+08:00',
          authorized: true,
          challengeId: 'challenge-live-1',
          code: 'AUTHORIZED',
          configVersion: 3,
          message: '授权成功',
          planId: 'plan-live-1',
          success: true,
        },
      },
      error: undefined,
    });

    render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="601318.SH"
        onFinishedEditing={vi.fn()}
        onSaved={onSaved}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));
    await user.type(screen.getByLabelText('计划卖出数量'), '300');
    await user.click(screen.getByRole('radio', { name: /手工填写每股全成本/ }));
    await user.type(screen.getByLabelText('每股全成本（元）'), '12.5');
    await user.selectOptions(screen.getByLabelText('模式'), 'live');
    await user.click(screen.getByLabelText('保存后预览并授权自动实盘卖出'));
    await user.click(screen.getByRole('button', { name: '保存并预览授权' }));

    expect(mocks.createPlan).toHaveBeenCalledWith({
      input: expect.objectContaining({
        autoExitAuthorized: false,
        executionMode: 'live',
        idempotencyKey: expect.any(String),
        instrumentCode: '601318.SH',
        protectedVolume: 300,
      }),
    });
    expect(mocks.previewAuthorization).toHaveBeenCalledWith({
      input: {
        accountId: '300000013250',
        expectedConfigVersion: 3,
        idempotencyKey: expect.any(String),
        planId: 'plan-live-1',
      },
    });
    expect(
      screen.getByRole('heading', { name: '确认自动实盘卖出授权' })
    ).toBeVisible();
    expect(screen.getByText('当前可卖').nextElementSibling).toHaveTextContent(
      '800 股'
    );
    expect(
      screen.getByText('T+1 暂不可卖').nextElementSibling
    ).toHaveTextContent('0 股');
    expect(screen.getByText('确认不创建委托')).toBeVisible();
    expect(mocks.confirmAuthorization).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole('button', { name: '确认授权自动实盘卖出' })
    );

    await waitFor(() => {
      expect(mocks.confirmAuthorization).toHaveBeenCalledWith({
        input: {
          accountId: '300000013250',
          challengeId: 'challenge-live-1',
          confirmationToken: 'confirmation-token-live-1',
          expectedConfigVersion: 3,
          idempotencyKey:
            mocks.previewAuthorization.mock.calls[0][0].input.idempotencyKey,
          planId: 'plan-live-1',
        },
      });
    });
    expect(onSaved).toHaveBeenCalledTimes(2);
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: '自动实盘卖出已授权' })
    );
  });
});

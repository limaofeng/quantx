import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ManualPlanEditor } from '@/features/portfolio/components/SellManagementPanels';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  refetch: vi.fn(),
  toast: vi.fn(),
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
    mocks.useMutation.mockReturnValue([
      { data: undefined, error: undefined, fetching: false },
      mocks.mutate,
    ]);
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
    mocks.mutate.mockResolvedValue({ data: {}, error: undefined });
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
    await user.click(screen.getByRole('button', { name: '创建卖出计划' }));

    expect(mocks.mutate).toHaveBeenCalledWith({
      input: expect.objectContaining({
        executionMode: 'paper',
        instrumentCode: '605499.SH',
        protectedVolume: 100,
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
});

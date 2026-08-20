import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  EntryPlansPage,
  type EntryPlanController,
  type EntryPlanWorkspaceView,
} from '@/features/entry-plans';

function makeController(
  overrides: Partial<EntryPlanController> = {}
): EntryPlanController {
  return {
    cancelPlan: vi.fn().mockResolvedValue(undefined),
    evaluatePlan: vi.fn().mockResolvedValue(undefined),
    pausePlan: vi.fn().mockResolvedValue(undefined),
    previewPendingIntent: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
    rejectPendingIntent: vi.fn().mockResolvedValue(undefined),
    resumePlan: vi.fn().mockResolvedValue(undefined),
    saveDraft: vi.fn().mockResolvedValue(undefined),
    searchSecurities: vi.fn().mockResolvedValue([]),
    setGlobalAutoEntryPaused: vi.fn().mockResolvedValue(undefined),
    triggerManualRule: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function makeView(
  overrides: Partial<EntryPlanWorkspaceView> = {}
): EntryPlanWorkspaceView {
  return {
    availableCashCny: 128000,
    dataUpdatedAt: '2026-08-20T10:00:00+08:00',
    events: [
      {
        amountCny: 5000,
        description: '券商成交回报已收敛，本批真实买入 100 股。',
        id: 'event-1',
        instrumentCode: '605499.SH',
        instrumentName: '东鹏饮料',
        kind: 'TRADE_FILLED',
        occurredAt: '2026-08-20T09:45:00+08:00',
        title: '真实买入成交',
        traceId: 'trace-001',
        volume: 100,
      },
    ],
    globalAutoEntryPaused: false,
    pendingIntents: [
      {
        bucket: 'core',
        candidateVolume: 100,
        cashBufferPct: 20,
        currentAskPrice: 123.5,
        dailyFilledAmountCny: 5000,
        expectedAmountCny: 12350,
        expiresAt: '2099-08-20T10:05:00+08:00',
        id: 'intent-1',
        instrumentCode: '605499.SH',
        instrumentName: '东鹏饮料',
        planFilledAmountCny: 5000,
        planId: 'plan-1',
        referencePrice: 123,
        riskAction: '允许 100 股，保留 20% 现金缓冲',
        signalAt: '2026-08-20T10:00:00+08:00',
        strategy: 'TREND_PULLBACK_CONFIRMATION',
      },
    ],
    plans: [
      {
        authorizationLabel: '模拟自动执行',
        bucket: 'core',
        currentPositionPct: 8,
        dailyRemainingAmountCny: 5000,
        expiresAt: '2026-09-20T15:00:00+08:00',
        exitProtectionEnabled: true,
        filledAmountCny: 5000,
        hasPendingApproval: true,
        hasWorkingOrder: false,
        id: 'plan-1',
        instrumentCode: '605499.SH',
        instrumentName: '东鹏饮料',
        lastDecision: '趋势成立，等待回撤企稳',
        latestPrice: 123.47,
        maxBuyPrice: 128,
        maxTotalAmountCny: 20000,
        nextEvaluationAt: '2026-08-20T10:01:00+08:00',
        status: 'ARMED',
        strategy: 'TREND_PULLBACK_CONFIRMATION',
        targetPositionPct: 20,
      },
    ],
    runtimeMessage: 'QMT 就绪 · 行情新鲜',
    todayFilledAmountCny: 5000,
    ...overrides,
  };
}

describe('EntryPlansPage', () => {
  it('renders the complete workbench without native strategy selects or raw JSON', () => {
    const { container } = render(
      <EntryPlansPage controller={makeController()} view={makeView()} />
    );

    expect(screen.getByRole('heading', { name: '买入管理' })).toBeVisible();
    expect(screen.getByRole('tab', { name: '建仓/加仓计划' })).toBeVisible();
    expect(screen.getByRole('tab', { name: /待确认买入/ })).toBeVisible();
    expect(screen.getByRole('tab', { name: '买入记录' })).toBeVisible();
    expect(
      screen.getByRole('radiogroup', { name: '选择买入策略' })
    ).toBeVisible();
    expect(screen.getByRole('radio', { name: /趋势回撤建仓/ })).toHaveAttribute(
      'aria-checked',
      'true'
    );
    expect(screen.getByText(/系统自动：趋势评分/)).toBeVisible();
    expect(
      screen.getByRole('button', { name: '保存并保持暂停' })
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: /保存并启动模拟/ })
    ).toBeVisible();
    expect(container.querySelector('select')).toBeNull();
    expect(container.querySelector('textarea')).toBeNull();
    expect(container).not.toHaveTextContent('{"');
    expect(screen.getByTestId('entry-plans-page')).toHaveClass(
      'overflow-x-hidden'
    );
    expect(document.getElementById('entry-plan-bucket-core')).toBeDisabled();
    expect(screen.getByLabelText(/最高可买价/)).toHaveValue(128);
  });

  it('supports keyboard strategy and tab selection', async () => {
    const user = userEvent.setup();
    render(<EntryPlansPage controller={makeController()} view={makeView()} />);

    const trendStrategy = screen.getByRole('radio', {
      name: /趋势回撤建仓/,
    });
    trendStrategy.focus();
    await user.keyboard('{ArrowDown}');

    expect(screen.getByRole('radio', { name: /价格阶梯建仓/ })).toHaveAttribute(
      'aria-checked',
      'true'
    );

    const plansTab = screen.getByRole('tab', { name: '建仓/加仓计划' });
    plansTab.focus();
    await user.keyboard('{ArrowRight}');

    expect(screen.getByRole('tab', { name: /待确认买入/ })).toHaveAttribute(
      'aria-selected',
      'true'
    );
    expect(screen.getByText('确认并重新风控')).toBeVisible();
  });

  it('searches security master data and accepts an unheld stock', async () => {
    const user = userEvent.setup();
    const controller = makeController({
      searchSecurities: vi.fn().mockResolvedValue([
        {
          heldVolume: 0,
          instrumentCode: '600519.SH',
          instrumentName: '贵州茅台',
          latestPrice: 1418.1230000000003,
        },
      ]),
    });
    render(<EntryPlansPage controller={controller} view={makeView()} />);

    await user.type(screen.getByLabelText('搜索股票'), '茅台');

    await waitFor(() => {
      expect(controller.searchSecurities).toHaveBeenCalledWith('茅台');
    });
    const results = screen.getByRole('list', { name: '证券搜索结果' });
    expect(within(results).getByText('未持有')).toBeVisible();
    await user.click(within(results).getByRole('button', { name: /贵州茅台/ }));

    expect(screen.getByLabelText('股票')).toHaveValue('600519.SH');
    expect(screen.getByText(/贵州茅台 · 当前持仓 0 股/)).toBeVisible();
    expect(screen.getByLabelText(/最高可买价/)).toHaveValue(1418.123);
  });

  it('uses explicit save and execution actions', async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<EntryPlansPage controller={controller} view={makeView()} />);

    await user.click(screen.getByRole('button', { name: '保存并保持暂停' }));

    await waitFor(() => {
      expect(controller.saveDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          instrumentCode: '605499.SH',
          maxBuyPrice: 128,
        }),
        'SAVE_PAUSED'
      );
    });

    await user.click(screen.getByRole('radio', { name: /实盘逐笔确认/ }));
    expect(
      screen.getByRole('button', { name: /保存并开始监控/ })
    ).toBeVisible();
    await user.click(screen.getByRole('button', { name: /保存并开始监控/ }));

    await waitFor(() => {
      expect(controller.saveDraft).toHaveBeenLastCalledWith(
        expect.objectContaining({ executionScenario: 'LIVE_MANUAL' }),
        'START_LIVE_MANUAL'
      );
    });

    await user.click(screen.getByRole('radio', { name: /实盘自动托管/ }));
    expect(
      screen.getByRole('button', { name: /预览授权并启动/ })
    ).toBeVisible();
  });

  it('edits every visible price ladder level instead of synthesizing one hidden level', async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<EntryPlansPage controller={controller} view={makeView()} />);

    await user.click(screen.getByRole('radio', { name: /价格阶梯建仓/ }));

    await waitFor(() => {
      expect(screen.getAllByLabelText(/档位 \d+ 触发价/)).toHaveLength(4);
    });
    expect(screen.getByRole('button', { name: '添加档位' })).toBeVisible();
    expect(
      screen.getByRole('radiogroup', { name: '档位 1 批次单位' })
    ).toBeVisible();

    await user.click(screen.getByRole('button', { name: '保存并保持暂停' }));
    await waitFor(() => {
      expect(controller.saveDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          strategy: 'PRICE_LADDER',
          priceLadderLevels: expect.arrayContaining([
            expect.objectContaining({
              levelId: 'ladder-1',
              trancheMode: 'AMOUNT',
              triggerPrice: 128,
            }),
          ]),
        }),
        'SAVE_PAUSED'
      );
    });
  });

  it('uses the dedicated mutation only for a manual rule batch trigger', async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(
      <EntryPlansPage
        controller={controller}
        view={makeView({
          plans: [
            {
              ...makeView().plans[0],
              primaryRuleId: 'manual-entry-1',
              strategy: 'MANUAL_TRIGGER',
            },
          ],
        })}
      />
    );

    expect(
      screen.queryByRole('button', { name: '立即检查' })
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '触发本批检查' }));

    expect(controller.triggerManualRule).toHaveBeenCalledWith(
      'plan-1',
      'manual-entry-1'
    );
    expect(controller.evaluatePlan).not.toHaveBeenCalled();
  });

  it('surfaces rejected plan mutations instead of failing silently', async () => {
    const user = userEvent.setup();
    const controller = makeController({
      evaluatePlan: vi.fn().mockRejectedValue(new Error('计划版本已变化')),
    });
    render(<EntryPlansPage controller={controller} view={makeView()} />);

    await user.click(screen.getByRole('button', { name: '立即检查' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '计划版本已变化'
    );
  });

  it('renders concrete exit protection fields and server capability guidance', () => {
    render(
      <EntryPlansPage
        controller={makeController()}
        view={makeView({
          capabilities: {
            version: 'managed-entry-v9',
            targetModes: [
              {
                value: 'TARGET_POSITION_PCT',
                label: '服务端目标仓位',
                description: '只补权威持仓缺口。',
              },
            ],
            ruleTypes: [
              {
                ruleType: 'TREND_PULLBACK_CONFIRMATION',
                label: '服务端趋势回撤',
                category: 'TREND',
                description: '服务端规则说明',
                suitableFor: '服务端适用场景',
                warning: '服务端风险提示',
                fields: [],
                presets: [],
              },
            ],
          },
        })}
      />
    );

    expect(screen.getByRole('radio', { name: /服务端趋势回撤/ })).toBeVisible();
    expect(screen.getByText('风险提示：服务端风险提示')).toBeVisible();
    expect(screen.getByLabelText(/收益止盈/)).toHaveValue(10);
    expect(screen.getByLabelText(/追踪保护启动收益/)).toHaveValue(8);
    expect(screen.getByLabelText(/追踪回撤幅度/)).toHaveValue(3);
  });

  it('shows pending details and structured events without treating evaluation as a fill', async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<EntryPlansPage controller={controller} view={makeView()} />);

    await user.click(screen.getByRole('tab', { name: /待确认买入/ }));
    expect(screen.getByText('允许 100 股，保留 20% 现金缓冲')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '确认并重新风控' }));
    expect(controller.previewPendingIntent).toHaveBeenCalledWith('intent-1');

    await user.click(screen.getByRole('tab', { name: '买入记录' }));
    expect(
      screen.getByRole('list', { name: '买入计划事件时间线' })
    ).toBeVisible();
    expect(screen.getByText('真实买入成交')).toBeVisible();
    expect(screen.getByText('决策追踪 trace-001')).toBeVisible();
    expect(screen.queryByText(/"traceId"/)).not.toBeInTheDocument();
  });

  it('makes the global automatic-entry safety gate explicit', async () => {
    const user = userEvent.setup();
    const controller = makeController();
    const { rerender } = render(
      <EntryPlansPage controller={controller} view={makeView()} />
    );

    await user.click(screen.getByRole('button', { name: '暂停全部自动买入' }));
    expect(controller.setGlobalAutoEntryPaused).toHaveBeenCalledWith(true);

    rerender(
      <EntryPlansPage
        controller={controller}
        view={makeView({ globalAutoEntryPaused: true })}
      />
    );
    expect(
      screen.getByRole('button', { name: '恢复全部自动买入' })
    ).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent(
      '已有委托仍等待真实回报'
    );
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { type ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';

import {
  TTradeReplayAccountPanel,
  TTradeReplaySidebar,
} from './TTradeReplaySidebar';

vi.mock('urql', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('urql');
  return {
    ...actual,
    useQuery: vi.fn(() => [
      { data: undefined, error: undefined, fetching: false },
      vi.fn(),
    ]),
  };
});

vi.mock('@/features/strategies/components/StrategyInstrumentSelector', () => ({
  StrategyInstrumentSelector: ({ placeholder }: { placeholder?: string }) => (
    <button type="button">{placeholder || '选择股票'}</button>
  ),
}));

type ReplaySidebarContext = NonNullable<
  ComponentProps<typeof TTradeReplaySidebar>['context']
>;

function createContext(
  overrides: Partial<ReplaySidebarContext> = {}
): ReplaySidebarContext {
  return {
    accountId: '300000013250',
    activeRunId: '',
    asOf: '2026-08-21',
    cashAvailable: 50000,
    deletingHistory: false,
    editor: null,
    frozen: false,
    history: [],
    historyLoading: false,
    loading: false,
    message: '开始回放后，初始账户将冻结。',
    mode: 'VIEW',
    onCreate: vi.fn(),
    onDelete: vi.fn(),
    onHistoryRefresh: vi.fn(),
    onSelectRun: vi.fn(),
    positions: [],
    source: 'SNAPSHOT',
    totalAsset: 50000,
    ...overrides,
  };
}

describe('TTradeReplaySidebar', () => {
  it('uses the sidebar exclusively for replay records', () => {
    const onCreate = vi.fn();
    const onDelete = vi.fn();
    const onSelectRun = vi.fn();
    const historyItem = {
      progressPct: 100,
      runId: 'run-20260803',
      startTime: '2026-08-03T09:30:00+08:00',
      status: 'COMPLETED',
      tNetProfit: 110.38,
    };

    render(
      <TTradeReplaySidebar
        context={createContext({
          activeRunId: historyItem.runId,
          history: [historyItem],
          onCreate,
          onDelete,
          onSelectRun,
        })}
      />
    );

    expect(screen.getByText('回测记录')).toBeInTheDocument();
    expect(screen.queryByText('冻结初始账户')).not.toBeInTheDocument();
    expect(screen.queryByText('持仓明细')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新增' }));
    fireEvent.click(screen.getByRole('button', { name: /^2026-08-03已完成/ }));
    fireEvent.click(
      screen.getByRole('button', { name: '删除 2026-08-03 回测' })
    );

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onSelectRun).toHaveBeenCalledWith(historyItem.runId);
    expect(onDelete).toHaveBeenCalledWith(historyItem);
  });

  it('renders new replay account maintenance in the main account panel', () => {
    const onSourceChange = vi.fn();
    const context = createContext({
      editor: {
        manualCash: '',
        manualPositions: [],
        onAddPosition: vi.fn(),
        onCashChange: vi.fn(),
        onPositionChange: vi.fn(),
        onPositionRemove: vi.fn(),
        onSourceChange,
        previousTradingDate: '2026-08-21',
        requiresManualPortfolio: false,
        snapshotAvailable: true,
      },
      mode: 'CREATE',
    });

    render(<TTradeReplayAccountPanel context={context} />);

    expect(
      screen.getByRole('heading', { name: '配置初始回测账户' })
    ).toBeInTheDocument();
    expect(screen.getByText('新建')).toBeInTheDocument();
    expect(
      screen.getByRole('group', { name: '回测账户来源' })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '导入并编辑' })
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '手工组合' }));
    expect(onSourceChange).toHaveBeenCalledWith('MANUAL');
  });

  it('shows the selected replay frozen account as read-only details', () => {
    const context = createContext({
      cashAvailable: 18000,
      frozen: true,
      positions: [
        {
          avgPrice: 12.345,
          availableVolume: 300,
          instrumentName: '示例股份',
          marketValue: 4200,
          stockCode: '600000.SH',
          volume: 300,
        },
      ],
      totalAsset: 22200,
    });

    render(<TTradeReplayAccountPanel context={context} />);

    expect(
      screen.getByRole('heading', { name: '冻结初始账户' })
    ).toBeInTheDocument();
    expect(screen.getByText('示例股份')).toBeInTheDocument();
    expect(screen.getByText('600000.SH')).toBeInTheDocument();
    expect(screen.getByText('300 股')).toBeInTheDocument();
    expect(
      screen.queryByRole('group', { name: '回测账户来源' })
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('可用资金')).not.toBeInTheDocument();
  });

  it('routes manual account edits through the replay draft callbacks', () => {
    const onCashChange = vi.fn();
    const onPositionChange = vi.fn();
    const onPositionRemove = vi.fn();
    const context = createContext({
      editor: {
        manualCash: '50000',
        manualPositions: [
          {
            avgPrice: '12.345',
            instrumentName: '示例股份',
            stockCode: '600000.SH',
            volume: '300',
          },
        ],
        onAddPosition: vi.fn(),
        onCashChange,
        onPositionChange,
        onPositionRemove,
        onSourceChange: vi.fn(),
        previousTradingDate: '2026-08-21',
        requiresManualPortfolio: true,
        snapshotAvailable: false,
      },
      mode: 'CREATE',
      positions: [
        {
          avgPrice: 12.345,
          availableVolume: 300,
          instrumentName: '示例股份',
          marketValue: 3703.5,
          stockCode: '600000.SH',
          volume: 300,
        },
      ],
      source: 'MANUAL',
    });

    render(<TTradeReplayAccountPanel context={context} />);

    fireEvent.change(screen.getByLabelText('可用资金'), {
      target: { value: '60000' },
    });
    fireEvent.change(screen.getByLabelText('持仓股数'), {
      target: { value: '400' },
    });
    fireEvent.change(screen.getByLabelText('平均成本'), {
      target: { value: '12.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: '删除 600000.SH' }));

    expect(onCashChange).toHaveBeenCalledWith('60000');
    expect(onPositionChange).toHaveBeenCalledWith(0, 'volume', '400');
    expect(onPositionChange).toHaveBeenCalledWith(0, 'avgPrice', '12.5');
    expect(onPositionRemove).toHaveBeenCalledWith(0);
    expect(screen.getByText('缺少可审计的历史初始组合')).toBeInTheDocument();
  });
});

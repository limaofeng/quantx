import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  useTAssistantPaper,
  type PaperExecution,
  type PaperFacts,
} from '@/features/portfolio/hooks/useTAssistantPaper';

import { TAssistantPaperPanel } from './TAssistantPaperPanel';

vi.mock('@/features/portfolio/hooks/useTAssistantPaper', () => ({
  useTAssistantPaper: vi.fn(),
}));
const query = vi.mocked(useTAssistantPaper);
const at = '2026-09-03T01:30:00.000Z';
const acceptedAt = '2026-09-03T01:30:00.100Z';
const pageInfo = {
  hasNextPage: false,
  hasPreviousPage: false,
  startCursor: 'first',
  endCursor: 'last',
};
const execution: PaperExecution = {
  executionId: 'paper-current',
  environment: 'PAPER',
  status: 'RUNNING',
  entryReadiness: 'READY',
  entryReadinessReasons: [],
  entryReadinessAsOf: at,
  scorerMode: 'RULE_ONLY',
  configVersionId: 'config-7',
  frozenConfigVersion: 7,
  configSnapshotHash: 'hash-7',
  policyVersion: 'rule-v3',
  featureSchemaVersion: 1,
  createdAt: at,
  seedPresent: true,
  seedAsOf: at,
  seedSnapshotId: 'seed-1',
  snapshotAsOf: acceptedAt,
};
function state(
  overrides: Partial<ReturnType<typeof useTAssistantPaper>> = {}
): ReturnType<typeof useTAssistantPaper> {
  return {
    accountId: 'account-1',
    executionId: execution.executionId,
    section: 'opportunities',
    executions: [execution],
    execution,
    facts: { tAssistantPaperExecution: execution },
    listLoading: false,
    loading: false,
    error: null,
    executionPage: 1,
    factPage: 1,
    hasNextExecutionPage: false,
    hasNextFactPage: false,
    selectExecution: vi.fn(),
    selectSection: vi.fn(),
    previousExecutions: vi.fn(),
    nextExecutions: vi.fn(),
    previousFacts: vi.fn(),
    nextFacts: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  };
}

describe('PAPER 执行只读面板', () => {
  beforeEach(() => {
    query.mockReturnValue(state());
  });

  it('shows missing seed and authoritative readiness reasons without trading actions', () => {
    query.mockReturnValue(
      state({
        execution: {
          ...execution,
          seedPresent: false,
          seedAsOf: null,
          seedSnapshotId: null,
          entryReadiness: 'WARMING',
          entryReadinessReasons: [
            'PAPER_SEED_REQUIRED',
            'MARKET_WINDOW_WARMING',
          ],
        },
      })
    );
    render(<TAssistantPaperPanel accountId="account-1" />);
    expect(
      screen.getByText('未初始化，需要 PAPER 账户种子')
    ).toBeInTheDocument();
    expect(screen.getByText('PAPER_SEED_REQUIRED')).toBeInTheDocument();
    expect(screen.getByText('MARKET_WINDOW_WARMING')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: /下单|批准|初始化|保存|撤单|启动|暂停/,
      })
    ).not.toBeInTheDocument();
  });

  it('renders original candidate scores and distinct source/acceptance milliseconds', () => {
    const facts: PaperFacts = {
      tAssistantPaperExecution: execution,
      tAssistantPaperOpportunities: {
        pageInfo,
        nodes: [
          {
            evidenceId: 'evidence-original',
            candidateId: 'candidate-first',
            instrumentCode: '600000.SH',
            eventType: 'T_OPPORTUNITY_CANDIDATE_FROZEN',
            evaluatedAt: at,
            createdAt: acceptedAt,
            frozenEvidencePresent: true,
            sourceAt: at,
            acceptedAt,
            score: 82.25,
            reasonCodes: ['ORIGINAL_CANDIDATE'],
            candidateFingerprint: 'fingerprint-original',
          },
        ],
      },
    };
    query.mockReturnValue(state({ facts }));
    render(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByRole('button', { name: '原冻结候选' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    const row = screen.getByText('candidate-first').closest('tr');
    expect(row).not.toBeNull();
    expect(within(row!).getByText('82.25')).toBeInTheDocument();
    expect(
      within(row!).getByText('2026/09/03 09:30:00.000')
    ).toBeInTheDocument();
    expect(within(row!).getByText(/09:30:00\.100$/)).toBeInTheDocument();
    expect(screen.getByText('ORIGINAL_CANDIDATE')).toBeInTheDocument();
  });

  it('shows allocated caps, frozen rank and decision reasons instead of raw JSON', () => {
    query.mockReturnValue(
      state({
        section: 'allocations',
        facts: {
          tAssistantPaperExecution: execution,
          tAssistantPaperAllocations: {
            pageInfo,
            nodes: [
              {
                allocationBatchId: 'allocation-1',
                cycleId: 'cycle-1',
                allocationAttempt: 2,
                status: 'COMMITTED',
                createdAt: at,
                committedAt: acceptedAt,
                expiresAt: acceptedAt,
                terminalReason: null,
                reasonCodes: ['INDUSTRY_CAP'],
                decisionId: 'decision-1',
                instrumentCode: '600000.SH',
                candidateId: 'candidate-1',
                rank: 1,
                action: 'CAP',
                requestedAmountCeiling: 10000,
                allocatedAmountCap: 5000,
                nextEligibleAt: null,
              },
            ],
          },
        },
      })
    );
    render(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('缩减额度')).toBeInTheDocument();
    expect(screen.getByText('INDUSTRY_CAP')).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveTextContent('5,000.00');
    expect(screen.queryByText(/"allocatedAmountCap"/)).not.toBeInTheDocument();
  });

  it('distinguishes BUY/SELL, fills and absent last-event source time', () => {
    const common = {
      intentId: 'intent-1',
      instrumentCode: '600000.SH',
      ownerId: 'paper-current',
      volume: 100,
      limitPrice: 10,
      submittedAt: at,
      expiresAt: acceptedAt,
      sourceAt: null,
      acceptedAt,
    };
    query.mockReturnValue(
      state({
        section: 'orders',
        facts: {
          tAssistantPaperExecution: execution,
          tAssistantPaperOrders: {
            pageInfo,
            nodes: [
              {
                ...common,
                orderId: 'buy-1',
                ownerType: 'T_ASSISTANT_EXECUTION',
                side: 'BUY',
                status: 'PARTIAL_FILLED',
                filledVolume: 50,
              },
              {
                ...common,
                orderId: 'sell-1',
                ownerType: 'EXIT_PLAN',
                side: 'SELL',
                status: 'SUBMITTED',
                filledVolume: 0,
              },
            ],
          },
        },
      })
    );
    render(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByText('买入 BUY')).toHaveClass('text-market-up');
    expect(screen.getByText('卖出 SELL')).toHaveClass('text-market-down');
    expect(screen.getByText('50 / 100 股')).toBeInTheDocument();
    expect(
      screen.getByRole('columnheader', { name: '最近回报行情源时间' })
    ).toBeInTheDocument();
    expect(
      within(screen.getByText('buy-1').closest('tr')!).getByText('未记录')
    ).toBeInTheDocument();
  });

  it('renders exit protection and capacity failure without exposing an exit action', () => {
    query.mockReturnValue(
      state({
        section: 'exitPlans',
        facts: {
          tAssistantPaperExecution: execution,
          tAssistantPaperExitPlans: {
            pageInfo,
            nodes: [
              {
                planId: 'plan-1',
                instrumentCode: '600000.SH',
                status: 'ACTIVE',
                protectedVolume: 100,
                exitedVolume: 50,
                remainingVolume: 50,
                capacityStatus: 'RECONCILE',
                capacityError: 'PAPER_CAPACITY_CONFLICT',
                lastError: null,
                lastEvaluatedAt: at,
              },
            ],
          },
        },
      })
    );
    render(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByText('100 / 50 / 50 股')).toBeInTheDocument();
    expect(screen.getByText('PAPER_CAPACITY_CONFLICT')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /卖出|退出执行/ })
    ).not.toBeInTheDocument();
  });

  it('offers explicit historical selection, section pagination and refresh', () => {
    const model = state({
      executions: [
        execution,
        { ...execution, executionId: 'paper-history', status: 'STOPPED' },
      ],
      hasNextExecutionPage: true,
      hasNextFactPage: true,
      factPage: 2,
    });
    query.mockReturnValue(model);
    render(<TAssistantPaperPanel accountId="account-1" />);
    fireEvent.change(screen.getByLabelText('选择 PAPER 执行'), {
      target: { value: 'paper-history' },
    });
    expect(model.selectExecution).toHaveBeenCalledWith('paper-history');
    fireEvent.click(screen.getByRole('button', { name: '下一页执行' }));
    expect(model.nextExecutions).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '下一页记录' }));
    fireEvent.click(screen.getByRole('button', { name: '上一页记录' }));
    expect(model.nextFacts).toHaveBeenCalledOnce();
    expect(model.previousFacts).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '委托与成交' }));
    expect(model.selectSection).toHaveBeenCalledWith('orders');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(model.refresh).toHaveBeenCalledOnce();
  });

  it('handles loading, empty records and error retry', () => {
    query.mockReturnValue(
      state({
        executionId: '',
        executions: [],
        execution: null,
        listLoading: true,
      })
    );
    const { rerender } = render(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByRole('status')).toHaveTextContent('正在加载 PAPER 执行');
    query.mockReturnValue(
      state({ executionId: '', executions: [], execution: null })
    );
    rerender(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByRole('status')).toHaveTextContent(
      '当前账户暂无 PAPER 执行记录'
    );
    const failed = state({ error: 'network unavailable' });
    query.mockReturnValue(failed);
    rerender(<TAssistantPaperPanel accountId="account-1" />);
    expect(screen.getByRole('alert')).toHaveTextContent('network unavailable');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(failed.refresh).toHaveBeenCalledOnce();
  });
});

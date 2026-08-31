import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type {
  ReplayEvidenceController,
  ReplaySignal,
} from '../../hooks/useTTradeReplayEvidence';

import type { ReplayEvidenceInfo } from './ReplayEvidenceChrome';
import {
  replayIntentTarget,
  replayReasonLabel,
} from './replayEvidencePresentation';
import { TTradeReplayDecisionAudit } from './TTradeReplayDecisionAudit';
import { TTradeReplaySignals } from './TTradeReplaySignals';

const info: ReplayEvidenceInfo = {
  runId: 'run-1',
  backtestId: 'bt-1',
  backtestVersion: 1,
  availability: 'AVAILABLE' as ReplayEvidenceInfo['availability'],
  source: 'VERSION_ARCHIVE' as ReplayEvidenceInfo['source'],
  sealed: true,
  contentFingerprint: 'fingerprint',
  reasonCode: null,
};

const signal: ReplaySignal = {
  id: 'evaluation-1',
  eventKey: 'exact-event-key',
  category: 'SIGNAL',
  candidateId: 'candidate-1',
  linkedIntentId: null,
  accountId: 'account-1',
  runId: 'run-1',
  stockCode: '600000.SH',
  eventKind: 'MATERIAL' as ReplaySignal['eventKind'],
  eventType: 'CANDIDATE_SUPPRESSED',
  evaluatedAt: '2026-08-28T02:00:00Z',
  windowStartedAt: null,
  windowEndedAt: null,
  coalescedCount: 1,
  policyVersion: '3',
  schemaVersion: '3',
  contentFingerprint: 'fingerprint',
  signalSnapshot: null,
};

function controller(
  overrides: Partial<ReplayEvidenceController> = {}
): ReplayEvidenceController {
  return {
    evaluations: [signal],
    auditRecords: [],
    signalFilters: {},
    auditFilters: {},
    signalPage: {
      evidence: info,
      items: [],
      summary: {
        eventCount: 1,
        candidateCount: 1,
        linkedIntentCount: 0,
        suppressedCount: 1,
      },
      pageInfo: { hasNextPage: true, endCursor: 'next' },
    },
    auditPage: {
      evidence: info,
      items: [],
      summary: {
        decisionCount: 0,
        withIntentCount: 0,
        noIntentCount: 0,
        riskBlockedCount: 0,
      },
      pageInfo: { hasNextPage: false, endCursor: null },
    },
    setSignalFilters: vi.fn(),
    setAuditFilters: vi.fn(),
    focusSignalEvent: vi.fn(),
    refresh: vi.fn(),
    refreshSignals: vi.fn(),
    refreshAudit: vi.fn(),
    signalsLoading: false,
    auditLoading: false,
    signalError: undefined,
    auditError: undefined,
    loadMoreSignals: vi.fn(),
    loadMoreAudit: vi.fn(),
    ...overrides,
  };
}

const names = new Map([['600000.SH', '测试股票']]);

describe('replay evidence pages', () => {
  it('shows an audit-linked context event explicitly as non-signal evidence', () => {
    const state = controller({
      signalFilters: { eventKey: 'policy-event', includeContext: true },
      evaluations: [
        {
          ...signal,
          eventKey: 'policy-event',
          category: 'CONTEXT',
          eventType: 'POLICY_CHANGED',
        },
      ],
    });
    render(
      <TTradeReplaySignals
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewAudit={vi.fn()}
      />
    );
    expect(
      screen.getByRole('heading', { name: '关联评估证据' })
    ).toBeInTheDocument();
    expect(screen.getByText('上下文事件 · 非交易信号')).toBeInTheDocument();
    expect(screen.queryByText('信号事件')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /策略配置变更 600000.SH/ })
    ).toHaveAttribute('aria-expanded', 'true');
    expect(
      screen.getByRole('button', { name: /策略配置变更 600000.SH/ })
    ).toHaveFocus();
  });
  it('explains known audit reasons while retaining unknown evidence codes', () => {
    expect(replayReasonLabel('MONITOR_ENGINE_EXIT_PLAN')).toBe(
      '持续监控已有批次的退出计划'
    );
    expect(replayReasonLabel('profit_armed')).toBe('止盈保护已激活');
    expect(replayReasonLabel('NEW_DOMAIN_REASON')).toBe('NEW_DOMAIN_REASON');
  });

  it('shows a real suppressed signal without an intent, and links by exact event key', () => {
    const state = controller();
    const onViewAudit = vi.fn();
    render(
      <TTradeReplaySignals
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewAudit={onViewAudit}
      />
    );
    expect(screen.getByText('未关联意图')).toBeInTheDocument();
    expect(screen.getByText('候选已抑制')).toBeInTheDocument();
    expect(screen.queryByText('方向 / 数量')).not.toBeInTheDocument();
    const row = screen.getByRole('button', { name: /候选已抑制 600000.SH/ });
    expect(row).toHaveClass('focus-visible:ring-blue-400/70');
    fireEvent.click(row);
    expect(row).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: '查看决策审计' }));
    expect(onViewAudit).toHaveBeenCalledWith('exact-event-key');
    expect(
      screen.queryByRole('button', { name: /确认买入|拒绝信号/ })
    ).not.toBeInTheDocument();
    const auditLink = screen.getByRole('button', { name: '查看决策审计' });
    auditLink.focus();
    fireEvent.keyDown(auditLink, { key: 'Escape' });
    expect(row).toHaveAttribute('aria-expanded', 'false');
    expect(row).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(state.loadMoreSignals).toHaveBeenCalledOnce();
  });

  it('distinguishes missing legacy evidence from a true empty result', () => {
    const state = controller({ evaluations: [] });
    state.signalPage = {
      ...state.signalPage!,
      evidence: {
        ...info,
        availability: 'UNAVAILABLE' as ReplayEvidenceInfo['availability'],
        sealed: false,
        reasonCode: 'SIGNAL_ARCHIVE_NOT_RECORDED',
      },
    };
    render(
      <TTradeReplaySignals
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewAudit={vi.fn()}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('未保存真实信号归档');
    expect(
      screen.queryByText(/该版本没有产生真实信号事件/)
    ).not.toBeInTheDocument();
    expect(screen.queryByText('已显示 0 / 1 条')).not.toBeInTheDocument();
  });

  it('keeps a no-intent material decision useful without internal reason tags', () => {
    const state = controller({
      auditRecords: [
        {
          decision: {
            id: 'decision-1',
            instanceId: 'run-1',
            traceId: 'trace-1',
            decidedAt: '2026-08-28T02:00:00Z',
            inputSummary: { instrument_code: '600000.SH' },
            outputSummary: {},
            statePatch: {},
            tradeIntents: [],
            decisionTrace: {},
            reason: 'MINIMUM_COVERAGE_NOT_REACHED',
            tags: ['strategy_output'],
          },
          evaluationEventKeys: ['exact-event-key'],
          executions: [],
        },
      ],
    });
    const onViewSignal = vi.fn();
    render(
      <TTradeReplayDecisionAudit
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewSignal={onViewSignal}
      />
    );
    expect(screen.getByText('无交易意图')).toBeInTheDocument();
    expect(
      screen.getByText('行情窗口覆盖不足，继续积累样本')
    ).toBeInTheDocument();
    expect(screen.queryByText('strategy_output')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /决策 600000.SH/ }));
    expect(screen.getByText(/定量、委托与成交不适用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /返回信号/ }));
    expect(onViewSignal).toHaveBeenCalledWith('exact-event-key');
    const signalLink = screen.getByRole('button', { name: /返回信号/ });
    signalLink.focus();
    fireEvent.keyDown(signalLink, { key: 'Escape' });
    const row = screen.getByRole('button', { name: /决策 600000.SH/ });
    expect(row).toHaveAttribute('aria-expanded', 'false');
    expect(row).toHaveFocus();
  });

  it('focuses the exact audit row after following a signal across pages', () => {
    const state = controller({
      auditFilters: { eventKey: 'page-two-event' },
      auditRecords: [
        {
          decision: {
            id: 'page-two-decision',
            instanceId: 'run-1',
            traceId: 'trace-page-two',
            decidedAt: '2026-08-28T02:00:00Z',
            inputSummary: { instrument_code: '600000.SH' },
            outputSummary: {},
            statePatch: {},
            tradeIntents: [],
            decisionTrace: {},
            reason: 'MINIMUM_COVERAGE_NOT_REACHED',
            tags: [],
          },
          evaluationEventKeys: ['page-two-event'],
          executions: [],
        },
      ],
    });
    render(
      <TTradeReplayDecisionAudit
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewSignal={vi.fn()}
      />
    );
    const row = screen.getByRole('button', { name: /决策 600000.SH/ });
    expect(row).toHaveAttribute('aria-expanded', 'true');
    expect(row).toHaveFocus();
  });

  it('does not label an amount or portfolio weight as share quantity', () => {
    expect(replayIntentTarget({ targetVolume: 100 })).toBe('100 股');
    expect(replayIntentTarget({ targetAmount: 2000 })).toBe('¥2,000.00');
    expect(replayIntentTarget({ targetPositionPct: 0.1 })).toBe('仓位 10.00%');
    expect(replayIntentTarget({})).toBe('由定量层确定');
  });

  it('shows loading and request failures without declaring a true empty result', () => {
    const state = controller({
      evaluations: [],
      signalPage: undefined,
      signalsLoading: true,
    });
    const props = {
      hasReplay: true,
      instrumentNames: names,
      onViewAudit: vi.fn(),
    };
    const { rerender } = render(
      <TTradeReplaySignals controller={state} {...props} />
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '正在读取当前版本证据'
    );
    expect(screen.queryByText(/没有产生真实信号事件/)).not.toBeInTheDocument();

    rerender(
      <TTradeReplaySignals
        controller={{
          ...state,
          signalsLoading: false,
          signalError: '读取失败',
        }}
        {...props}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('读取失败');
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    expect(state.refreshSignals).toHaveBeenCalledOnce();

    rerender(
      <TTradeReplaySignals
        controller={controller({ evaluations: [] })}
        {...props}
      />
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '该版本没有产生真实信号事件'
    );
  });

  it('does not turn a trace JSON object or technical output label into a business reason', () => {
    const state = controller({
      auditRecords: [
        {
          decision: {
            id: 'decision-no-reason',
            instanceId: 'run-1',
            traceId: 'trace-1',
            decidedAt: '2026-08-28T02:00:00Z',
            inputSummary: { instrument_code: '600000.SH' },
            outputSummary: { reason: 'strategy_output' },
            statePatch: {},
            tradeIntents: [],
            decisionTrace: { tags: ['strategy_output'], trace_id: 'trace-1' },
            reason: null,
            tags: ['strategy_output'],
          },
          evaluationEventKeys: [],
          executions: [],
        },
      ],
    });
    render(
      <TTradeReplayDecisionAudit
        controller={state}
        hasReplay
        instrumentNames={names}
        onViewSignal={vi.fn()}
      />
    );
    expect(screen.getByText('本次决策未产生 TradeIntent')).toBeInTheDocument();
    expect(screen.queryByText(/strategy_output/)).not.toBeInTheDocument();
  });
});

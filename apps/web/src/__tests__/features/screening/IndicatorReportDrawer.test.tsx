import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { IndicatorReportDrawer } from '@/features/screening/components/IndicatorReportDrawer';
import type { StockIndicatorReportMatchesQuery } from '@/generated/gql/graphql';

vi.mock('@/features/screening/hooks/useIndicatorResearch', () => ({
  useIndicatorReport: () => ({
    detail: null,
    fetching: false,
    refresh: vi.fn(),
  }),
}));

const referenceMatch: StockIndicatorReportMatchesQuery['stockIndicatorReportMatches'][number] =
  {
    requestId: 'joint',
    status: 'REFERENCE_ONLY',
    reason: '历史 ST 标记不可还原',
    reports: [],
    configJson: {},
    command: 'uv run quantx-research run --config indicator-study.json',
    blockers: ['排除当前 ST 不可用作历史筛选'],
  };

function FocusHarness({ removeTriggerOnOpen = false } = {}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('1.2');
  return (
    <>
      <input
        aria-label="条件草稿"
        value={draft}
        onChange={event => setDraft(event.target.value)}
      />
      {!(open && removeTriggerOnOpen) && (
        <button onClick={() => setOpen(true)}>查看组合报告</button>
      )}
      <IndicatorReportDrawer
        focus={open ? 'joint' : null}
        onClose={() => setOpen(false)}
        criteria={{}}
        indicators={[]}
        loading={false}
        onRefresh={vi.fn()}
        pending
      />
    </>
  );
}

describe('IndicatorReportDrawer', () => {
  it.each(['Escape', 'close button'])(
    'restores the report trigger focus after %s without resetting a draft',
    async closeMethod => {
      render(<FocusHarness />);
      const draftInput = screen.getByRole('textbox', { name: '条件草稿' });
      fireEvent.change(draftInput, { target: { value: '2.5' } });
      const trigger = screen.getByRole('button', { name: '查看组合报告' });
      trigger.focus();
      fireEvent.click(trigger);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(trigger).not.toHaveFocus();

      if (closeMethod === 'Escape') {
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      } else {
        fireEvent.click(screen.getByRole('button', { name: '关闭研究报告' }));
      }

      await waitFor(() => expect(trigger).toHaveFocus());
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(draftInput).toHaveValue('2.5');
    }
  );

  it('does not focus a removed report trigger when closing', async () => {
    render(<FocusHarness removeTriggerOnOpen />);
    const trigger = screen.getByRole('button', { name: '查看组合报告' });
    trigger.focus();
    fireEvent.click(trigger);
    const focus = vi.spyOn(trigger, 'focus');
    fireEvent.click(screen.getByRole('button', { name: '关闭研究报告' }));

    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    );
    expect(focus).not.toHaveBeenCalled();
    expect(trigger.isConnected).toBe(false);
  });

  it('makes reference-only and unsupported conditions explicit without offering execution', () => {
    const close = vi.fn();
    render(
      <IndicatorReportDrawer
        focus="joint"
        onClose={close}
        criteria={{}}
        indicators={[]}
        match={referenceMatch}
        loading={false}
        onRefresh={vi.fn()}
        pending
      />
    );
    expect(
      screen.getByText('仅供参考：不是当前条件的精确报告')
    ).toBeInTheDocument();
    expect(screen.getByText('历史 ST 标记不可还原')).toBeInTheDocument();
    expect(screen.getByText(/对应未应用的条件草稿/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载研究配置' })).toBeDisabled();
    expect(screen.queryByText(referenceMatch.command)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭研究报告' }));
    expect(close).toHaveBeenCalledOnce();
  });
  it('shows an offline command for missing supported research without a run action', () => {
    render(
      <IndicatorReportDrawer
        focus="joint"
        onClose={vi.fn()}
        criteria={{}}
        indicators={[]}
        match={{
          ...referenceMatch,
          status: 'MISSING',
          reason: null,
          blockers: [],
        }}
        loading={false}
        onRefresh={vi.fn()}
        pending={false}
      />
    );
    expect(screen.getByText('尚无匹配报告')).toBeInTheDocument();
    expect(screen.getByText(referenceMatch.command)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载研究配置' })).toBeEnabled();
    expect(
      screen.queryByRole('button', { name: /执行|运行分析|生成报告/ })
    ).not.toBeInTheDocument();
  });

  it('shows blockers once when already included in the report reason without enabling blocked config downloads', () => {
    const stBlocker = '历史 ST 状态尚未还原，不能执行排除历史 ST 的精确研究';
    const industryBlocker = '历史行业归属尚未还原';
    render(
      <IndicatorReportDrawer
        focus="joint"
        onClose={vi.fn()}
        criteria={{}}
        indicators={[]}
        match={{
          ...referenceMatch,
          reason: `${stBlocker}；参考报告未应用当前过滤`,
          blockers: [stBlocker, industryBlocker, industryBlocker],
        }}
        loading={false}
        onRefresh={vi.fn()}
        pending={false}
      />
    );

    expect(screen.getAllByText(stBlocker, { exact: false })).toHaveLength(1);
    expect(screen.getAllByText(industryBlocker)).toHaveLength(1);
    expect(screen.getByRole('button', { name: '下载研究配置' })).toBeDisabled();
    expect(screen.queryByText(referenceMatch.command)).not.toBeInTheDocument();
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ScreeningTopBar } from '@/features/screening/components/ScreeningTopBar';
import type {
  IndicatorDefinition,
  ScreeningCriteria,
} from '@/features/screening/types';

const indicators: IndicatorDefinition[] = [
  {
    id: 'change_pct',
    label: '当日涨跌幅',
    category: '价格',
    description: '收盘变化',
    unit: '%',
    lookback: 2,
    kind: 'numeric',
    operators: ['gte', 'lte', 'between'],
    version: 'daily-indicator-v1',
    researchSupported: true,
  },
  {
    id: 'kdj_cross_up',
    label: 'KDJ金叉',
    category: '交叉',
    description: 'K 上穿 D',
    unit: '',
    lookback: 134,
    kind: 'binary',
    operators: ['eq'],
    version: 'daily-indicator-v1',
    researchSupported: true,
  },
  {
    id: 'roe_ttm',
    label: 'ROE TTM',
    category: '财务',
    description: '可见的财务指标',
    unit: '%',
    lookback: 0,
    kind: 'numeric',
    operators: ['gte', 'lte'],
    version: 'daily-indicator-v1',
    researchSupported: false,
    unsupportedReason: '历史财务覆盖待核验',
  },
];

function harness() {
  const apply = vi.fn();
  const report = vi.fn();
  const joint = vi.fn();
  function Harness() {
    const [criteria, setCriteria] = useState<ScreeningCriteria>({
      screeningMode: 'INDICATOR',
      universe: 'STOCK',
      excludeST: true,
      indicatorConditions: [],
    });
    return (
      <ScreeningTopBar
        screeningCriteria={criteria}
        setScreeningCriteria={setCriteria}
        availableIndustries={[]}
        meta={{
          total: 0,
          missingSnapshotDates: [],
          hasStaleData: false,
          isComplete: true,
          warnings: [],
        }}
        onRunScreening={() => apply(criteria)}
        screeningLoading={false}
        onReset={vi.fn()}
        onBackfillSnapshot={vi.fn()}
        onOpenAdvancedData={vi.fn()}
        snapshotBackfillLoading={false}
        indicators={indicators}
        onRetryCatalog={vi.fn()}
        onOpenIndicatorReport={report}
        onOpenJointReport={joint}
      />
    );
  }
  render(<Harness />);
  return { apply, report, joint };
}

describe('IndicatorControls', () => {
  it('starts without conditions, preserves zero and opens research without applying screening', () => {
    const callbacks = harness();
    expect(screen.getByLabelText('排除当前 ST')).toBeChecked();
    expect(screen.queryByLabelText('ROE TTM 数值')).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '查看 当日涨跌幅 报告' })
    );
    expect(callbacks.report).toHaveBeenCalledWith('change_pct');
    expect(callbacks.apply).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: '添加 当日涨跌幅 条件' })
    );
    expect(screen.getByRole('button', { name: '应用筛选' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('当日涨跌幅 数值'), {
      target: { value: '0' },
    });
    expect(screen.getByRole('button', { name: '应用筛选' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    expect(callbacks.apply).toHaveBeenCalledWith(
      expect.objectContaining({
        indicatorConditions: [
          { indicatorId: 'change_pct', operator: 'gte', value: 0 },
        ],
      })
    );
  });
  it('shows joint research for multiple indicators and retains unresearched indicators for screening', () => {
    const callbacks = harness();
    fireEvent.click(screen.getByRole('button', { name: '添加 KDJ金叉 条件' }));
    fireEvent.click(screen.getByRole('button', { name: '添加 ROE TTM 条件' }));
    expect(screen.getByText('可选股；历史研究未覆盖')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '查看当前组合报告' })
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText('ROE TTM 数值'), {
      target: { value: '-5' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查看当前组合报告' }));
    expect(callbacks.joint).toHaveBeenCalledOnce();
    expect(callbacks.apply).not.toHaveBeenCalled();
  });
});

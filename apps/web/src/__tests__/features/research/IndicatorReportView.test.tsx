import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { IndicatorReportView } from '@/features/research/components/IndicatorReportView';
import {
  indicatorGroupLabel,
  formatResearchRate,
} from '@/features/research/indicatorPresentation';
import type { IndicatorReportQuery } from '@/generated/gql/graphql';

export const indicatorReportFixture: NonNullable<
  IndicatorReportQuery['indicatorReport']
> = {
  reference: {
    runKey: 'a'.repeat(64),
    reportId: 'joint-1',
    kind: 'joint',
    indicatorIds: ['change_pct', 'volume_ratio'],
    studyId: 'indicator-study',
    version: 'v1',
    runId: 'run-1',
    completedAt: '2026-08-31',
    dataStart: '2021-08-01',
    dataEnd: '2026-08-31',
    configHash: 'hash',
    warnings: [],
  },
  indicatorVersion: 'daily-indicator-v1',
  universe: { instrument_type: 'STOCK' },
  horizons: Array.from({ length: 20 }, (_, index) => index + 1),
  returnBases: ['close', 'next_open'],
  conditions: [],
  definitions: [],
  coverage: {},
  distribution: [],
  configJson: {},
  warnings: ['历史股票池无法完整还原'],
  artifactErrors: [],
  rows: [1, 20].map(horizon => ({
    group: 'joint',
    horizon,
    returnBasis: 'close',
    period: 'all',
    sampleCount: horizon === 1 ? 100 : 50,
    stockCount: 10,
    dateCount: 8,
    upRate: 0.6,
    meanReturn: 0.01,
    medianReturn: 0,
    dateEqualUpRate: 0.55,
    dateEqualMeanReturn: 0.008,
    baselineUpRate: 0.54,
    upRateLift: 0.01,
    meanReturnLift: 0.002,
    ciLow: null,
    ciHigh: null,
    pValue: null,
    qValue: null,
    meanCiLow: null,
    meanCiHigh: null,
    meanPValue: null,
    meanQValue: null,
    inferenceStatus: 'insufficient_dates',
  })),
};

describe('IndicatorReportView', () => {
  it('uses the frozen confidence level instead of asserting a default interval', () => {
    render(
      <IndicatorReportView
        report={{
          ...indicatorReportFixture,
          configJson: { statistics: { confidence_level: 0.9 } },
        }}
      />
    );
    expect(screen.getAllByText(/90% 区间/)).toHaveLength(2);
    expect(screen.queryByText(/95% 区间/)).not.toBeInTheDocument();
  });
  it('shows independent horizon counts, uncertainty and no predicted stock probability', () => {
    render(<IndicatorReportView report={indicatorReportFixture} />);
    expect(screen.getByText(/不是个股预测概率/)).toBeInTheDocument();
    expect(screen.getAllByText('描述统计 / 推断不足')[0]).toBeInTheDocument();
    expect(screen.getByText('55.00% / 60.00%')).toBeInTheDocument();
    expect(screen.getByText('+1.00pp')).toHaveClass('text-market-up');
    expect(screen.getByText('10 / 8 / 100')).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole('group', { name: '后续交易日' })).getByRole(
        'button',
        { name: '20 日' }
      )
    );
    expect(screen.getByText('10 / 8 / 50')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '20 日' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByRole('button', { name: '20 日' })).toHaveClass(
      'bg-[var(--button-primary)]'
    );
    expect(screen.getByLabelText('价格起点')).toBeInTheDocument();
    expect(screen.getByText('历史股票池无法完整还原')).toBeInTheDocument();
  });
  it('does not convert missing or zero estimates into optimistic values', () => {
    expect(formatResearchRate(null)).toBe('—');
    expect(formatResearchRate(Number.NaN)).toBe('—');
    expect(formatResearchRate(0)).toBe('0.00%');
    expect(formatResearchRate(-0.01, true, 'pp')).toBe('-1.00pp');
    expect(
      indicatorGroupLabel(
        'condition_1',
        [{ indicator_id: 'rsi12', operator: 'lte', value: 30 }],
        [{ id: 'rsi12', label: 'RSI(12)' }]
      )
    ).toBe('RSI(12) ≤ 30');
  });
});

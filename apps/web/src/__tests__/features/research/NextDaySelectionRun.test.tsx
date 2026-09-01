import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { NextDaySelectionRun } from '@/features/research/components/NextDaySelectionRun';
import type { ResearchRunQuery } from '@/generated/gql/graphql';

const run: NonNullable<ResearchRunQuery['researchRun']> = {
  summary: {
    key: 'a'.repeat(64),
    runId: '20260901T120000Z-selection',
    studyId: 'next-day-selection',
    version: 'v1',
    status: 'success',
    startedAt: '2026-09-01T12:00:00Z',
    completedAt: '2026-09-01T12:10:00Z',
    eventCount: 1000,
    elapsedSeconds: 600,
    configHash: 'b'.repeat(64),
    hasMetrics: true,
    artifactErrors: [],
  },
  dataQuality: {
    sample_count: 1000,
    stock_count: 100,
    date_count: 240,
    data_start: '2021-09-01',
    data_end: '2026-08-31',
    historical_universe: { complete: true, coverage: 1 },
  },
  analysisSampleCount: null,
  eventCurve: [],
  interactionHeatmap: [],
  comparison: [],
  comparisonSensitivity: {},
  regressions: [],
  robustness: {},
  warnings: [],
  artifactErrors: [],
  indicatorReports: [],
  selectionMetrics: {
    selected_family: 'LOGISTIC',
    validation: {
      fold_count: 12,
      logistic_brier: 0.2,
      lightgbm_brier: 0.21,
    },
    frozen_test: {
      start: '2025-09-01',
      end: '2026-08-31',
      probability: { brier_skill: 0.2, ece: 0.02 },
      ranking: {
        top_20: {
          precision: 0.62,
          up_rate_lift: 0.12,
          up_rate_lift_ci_low: 0.05,
          mean_return_lift: 0.004,
        },
        top_50: {
          precision: 0.58,
          up_rate_lift: 0.08,
          mean_return_lift: 0.003,
        },
      },
    },
    gates: {
      brier_skill_positive: true,
      ece_within_3pct: true,
      top20_lift_ci_lower_positive: true,
      historical_universe_complete: true,
      active_eligible: true,
    },
  },
};

describe('NextDaySelectionRun', () => {
  it('renders frozen-test evidence and keeps the page explicitly non-trading', () => {
    render(<NextDaySelectionRun run={run} />);

    expect(
      screen.getByRole('heading', { name: '次日上涨概率模型证据' })
    ).toBeInTheDocument();
    expect(screen.getByText('LOGISTIC')).toBeInTheDocument();
    expect(screen.getByText('5.00%')).toBeInTheDocument();
    expect(screen.getByText('满足')).toBeInTheDocument();
    expect(
      screen.getByText(/不会生成策略、交易意图或订单/)
    ).toBeInTheDocument();
  });
});

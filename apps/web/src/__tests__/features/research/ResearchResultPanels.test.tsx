import { render, screen } from '@testing-library/react';

import {
  InteractionHeatmap,
  RegressionTable,
  SourceProvenancePanel,
  VolumeComparisonPanel,
} from '@/features/research/components';
import type { ComparisonStatistic } from '@/features/research/model';

function comparisonRow(
  patch: Partial<ComparisonStatistic> = {}
): ComparisonStatistic {
  return {
    benchmark: 'csi300',
    ci_high: 0.029,
    ci_low: 0.001,
    dimensions: {
      comparison: 'high_minus_low',
      price_position_bin: 'high_minus_low',
    },
    horizon: 5,
    normal_mean: 0,
    normal_median: 0,
    normal_sample_size: 80,
    p_value: 0.01,
    q_value: 0.04,
    return_kind: 'close_response',
    shock_mean: 0.02,
    shock_median: 0.018,
    shock_sample_size: 80,
    significant: true,
    spread_mean: 0.02,
    spread_median: 0.018,
    unique_dates: 60,
    ...patch,
  };
}

describe('research result panels', () => {
  it('renders an empty heatmap without leaking an infinite horizon', () => {
    render(<InteractionHeatmap rows={[]} />);

    expect(screen.getByText('暂无可用交互分组')).toBeInTheDocument();
    expect(screen.queryByText(/Infinity/)).not.toBeInTheDocument();
  });

  it('labels return kinds when regression models are mixed', () => {
    render(
      <RegressionTable
        models={[
          {
            coefficients: [
              {
                ci_high: 0.03,
                ci_low: 0.01,
                estimate: 0.02,
                p_value: 0.03,
                q_value: 0.08,
                significant: false,
                std_error: 0.005,
                t_stat: 4,
                term: 'log_rvol_x_position',
              },
            ],
            covariance: 'two_way_cluster',
            dependent_variable: 'csi300_excess_close_h5',
            horizon: 5,
            nobs: 500,
            r_squared: 0.1,
            return_kind: 'close_response',
            warnings: [],
          },
          {
            coefficients: [
              {
                ci_high: 0.025,
                ci_low: 0.005,
                estimate: 0.015,
                p_value: 0.04,
                q_value: 0.04,
                significant: true,
                std_error: 0.005,
                t_stat: 3,
                term: 'log_rvol_x_position',
              },
            ],
            covariance: 'two_way_cluster',
            dependent_variable: 'csi300_excess_next_open_h5',
            horizon: 5,
            nobs: 480,
            r_squared: 0.08,
            return_kind: 'next_open',
            warnings: [],
          },
        ]}
      />
    );

    expect(screen.getByText('收益口径')).toBeInTheDocument();
    expect(screen.getByText('收盘响应')).toBeInTheDocument();
    expect(screen.getByText('次日开盘')).toBeInTheDocument();
    expect(screen.getByText('FDR q')).toBeInTheDocument();
    expect(screen.getByText('未通过 FDR')).toBeInTheDocument();
    expect(screen.getByText('已确认')).toBeInTheDocument();
    expect(screen.getByText(/股票\/日期双向聚类/)).toBeInTheDocument();
  });

  it('renders an explicit empty normal-volume comparison state', () => {
    render(<VolumeComparisonPanel rows={[]} />);

    expect(screen.getByText('暂无正常成交量对照')).toBeInTheDocument();
  });

  it('renders audited QMT archive provenance without a local path', () => {
    render(
      <SourceProvenancePanel
        provenance={{
          archive_format: 'quantx-qmt-daily-bars-source-v1',
          emitted_rows: 5_470_541,
          kind: 'qmt-daily-bar-archive',
          ledger_sha256: 'a'.repeat(64),
          metadata_universe_validated: true,
          queries: [
            {
              available_end: '2026-07-29',
              available_start: '2020-03-13',
              boundary_truncated: true,
              requested_end: '2026-07-30',
              requested_start: '2020-03-13',
            },
            {
              available_end: '2026-07-28',
              available_start: '2020-03-14',
              boundary_truncated: false,
              requested_end: '2026-07-30',
              requested_start: '2020-03-13',
            },
          ],
          required_request_count: 180,
          selected_chunk_count: 1_132,
          selected_chunk_manifest_sha256: 'b'.repeat(64),
          selected_request_count: 180,
          selected_source_record_count: 5_470_541,
        }}
      />
    );

    expect(screen.getByText('数据来源与可复现证据')).toBeInTheDocument();
    expect(screen.getByText('QMT 日线归档')).toBeInTheDocument();
    expect(screen.getByText('已通过')).toBeInTheDocument();
    expect(
      screen.getByText(/2020-03-14 至 2026-07-28（边界截断）/)
    ).toBeInTheDocument();
    expect(screen.getByText('a'.repeat(64))).toBeInTheDocument();
    expect(screen.queryByText(/F:\\/)).not.toBeInTheDocument();
  });

  it('prefers market-relative comparison benchmarks', () => {
    render(
      <VolumeComparisonPanel
        rows={[
          comparisonRow({ benchmark: 'absolute' }),
          comparisonRow({ benchmark: 'market_equal_weight' }),
          comparisonRow({ benchmark: 'csi300' }),
        ]}
      />
    );

    expect(screen.getByLabelText('基准')).toHaveValue('csi300');
  });

  it('does not present a non-significant comparison as an effective result', () => {
    render(
      <VolumeComparisonPanel
        rows={[
          comparisonRow({
            ci_low: -0.01,
            q_value: 0.2,
            significant: false,
          }),
        ]}
      />
    );

    expect(screen.getByText('价格位置交互未形成有效结论')).toBeInTheDocument();
    expect(screen.queryByText('价格位置交互已确认')).not.toBeInTheDocument();
    expect(screen.getAllByText('未通过 FDR').length).toBeGreaterThan(0);
    expect(screen.getByText(/覆盖 60 个有效交易日/)).toBeInTheDocument();
  });

  it('shows q, confidence interval and unique dates for a confirmed interaction', () => {
    render(<VolumeComparisonPanel rows={[comparisonRow()]} />);

    expect(screen.getByText('价格位置交互已确认')).toBeInTheDocument();
    expect(screen.getAllByText('已确认').length).toBeGreaterThan(0);
    expect(screen.getByText(/FDR q=0.040/)).toBeInTheDocument();
    expect(screen.getByText(/覆盖 60 个有效交易日/)).toBeInTheDocument();
    expect(screen.getAllByText(/\[0.10%, 2.90%\]/).length).toBeGreaterThan(0);
  });

  it('summarizes sensitivity direction and significance without rendering its raw table', () => {
    render(
      <VolumeComparisonPanel
        rows={[comparisonRow()]}
        sensitivity={{
          cooldown_5d: [comparisonRow({ spread_mean: 0.01 })],
          cooldown_20d: [
            comparisonRow({
              ci_high: -0.001,
              ci_low: -0.03,
              q_value: 0.92,
              shock_mean: -0.123,
              significant: false,
              spread_mean: -0.02,
            }),
          ],
        }}
      />
    );

    expect(screen.getByText('冷却期敏感性摘要')).toBeInTheDocument();
    expect(screen.getByText('冷却期 5 日')).toBeInTheDocument();
    expect(screen.getByText('冷却期 20 日')).toBeInTheDocument();
    expect(screen.getByText('方向一致')).toBeInTheDocument();
    expect(screen.getByText('方向不一致')).toBeInTheDocument();
    expect(screen.getByText('显著性一致')).toBeInTheDocument();
    expect(screen.getByText('显著性不一致')).toBeInTheDocument();
    expect(screen.queryByText('-12.30%')).not.toBeInTheDocument();
    expect(screen.queryByText('0.920')).not.toBeInTheDocument();
  });
});

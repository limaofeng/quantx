import {
  buildResearchRunPath,
  isConfirmedComparison,
  isSmallSample,
  parseResearchResult,
  readResearchRunKey,
} from '@/features/research/model';

describe('research result model', () => {
  it('keeps the run identity in the route and the opaque lookup key separate', () => {
    const path = buildResearchRunPath(
      'volume-shock',
      'smoke-v1',
      'run-001',
      'opaque/key?value'
    );

    expect(path).toBe(
      '/research/volume-shock/smoke-v1/runs/run-001?key=opaque%2Fkey%3Fvalue'
    );
    expect(readResearchRunKey('?key=opaque%2Fkey%3Fvalue')).toBe(
      'opaque/key?value'
    );
  });

  it('marks smoke and low-event runs as small samples', () => {
    expect(isSmallSample('smoke-v1', 1000)).toBe(true);
    expect(isSmallSample('v1', 99)).toBe(true);
    expect(isSmallSample('v1', 100)).toBe(false);
  });

  it('parses supported statistics without trusting unknown JSON', () => {
    const result = parseResearchResult({
      analysisSampleCount: 12_000,
      comparison: [],
      comparisonSensitivity: {
        cooldown_5d: [],
        cooldown_20d: [],
      },
      dataQuality: {
        coverage: [
          {
            first_valid_time: '2024-01-02',
            last_valid_time: '2025-12-31',
            stock_code: '000001.SZ',
            valid_rows: 480,
          },
          {
            first_valid_time: null,
            last_valid_time: null,
            stock_code: '000002.SZ',
            valid_rows: 0,
          },
        ],
        is_usable: true,
        row_count: 900,
        source_provenance: {
          archive_format: 'quantx-qmt-daily-bars-source-v1',
          emitted_rows: 5_470_541,
          kind: 'qmt-daily-bar-archive',
          ledger_sha256: 'a'.repeat(64),
          metadata_universe_validated: true,
          queries: [
            {
              available_end: '2026-07-29',
              boundary_truncated: true,
              requested_end: '2026-07-30',
            },
          ],
          selected_request_count: 180,
        },
        valid_row_count: 801,
        warnings: [],
      },
      eventCurve: [
        {
          benchmark: 'csi300',
          ci_high: 0.02,
          ci_low: -0.01,
          horizon: 5,
          mean: 0.006,
          median: 0.004,
          positive_rate: 0.55,
          return_kind: 'close_response',
          sample_size: 120,
          unique_dates: 80,
        },
      ],
      interactionHeatmap: [],
      regressions: [],
      robustness: {},
    });

    expect(result.analysisSampleCount).toBe(12_000);
    expect(result.comparisonSensitivity).toEqual({
      cooldown_5d: [],
      cooldown_20d: [],
    });
    expect(result.dataQuality?.valid_row_count).toBe(801);
    expect(result.dataQuality?.coverage?.[0].valid_rows).toBe(480);
    expect(result.dataQuality?.source_provenance).toMatchObject({
      kind: 'qmt-daily-bar-archive',
      ledger_sha256: 'a'.repeat(64),
      metadata_universe_validated: true,
      selected_request_count: 180,
    });
    expect(
      result.dataQuality?.source_provenance?.queries?.[0].available_end
    ).toBe('2026-07-29');
    expect(result.eventCurve[0].sample_size).toBe(120);
    expect(result.eventCurve[0].unique_dates).toBe(80);
    expect(result.validationErrors).toEqual([]);
  });

  it('degrades invalid artifact sections to explicit empty states', () => {
    const result = parseResearchResult({
      analysisSampleCount: -1,
      comparison: [{ q_value: 'invalid' }],
      comparisonSensitivity: {
        cooldown_5d: [{ q_value: 'invalid' }],
      },
      dataQuality: { row_count: -1 },
      eventCurve: [{ horizon: 'bad' }],
      interactionHeatmap: 'not-a-table',
      regressions: null,
      robustness: [],
    });

    expect(result.dataQuality).toBeNull();
    expect(result.analysisSampleCount).toBeNull();
    expect(result.comparison).toEqual([]);
    expect(result.comparisonSensitivity).toEqual({});
    expect(result.eventCurve).toEqual([]);
    expect(result.heatmap).toEqual([]);
    expect(result.regressions).toEqual([]);
    expect(result.robustness).toEqual({});
    expect(result.validationErrors).toHaveLength(7);
  });

  it('only confirms comparisons with FDR and a confidence interval excluding zero', () => {
    const base = {
      benchmark: 'csi300' as const,
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
      return_kind: 'close_response' as const,
      shock_mean: 0.02,
      shock_median: 0.018,
      shock_sample_size: 80,
      significant: true,
      spread_mean: 0.02,
      spread_median: 0.018,
      unique_dates: 60,
    };
    const parsed = parseResearchResult({
      analysisSampleCount: 160,
      comparison: [base],
      comparisonSensitivity: {
        cooldown_5d: [base],
        cooldown_20d: [{ ...base, significant: false }],
      },
      dataQuality: null,
      eventCurve: [],
      interactionHeatmap: [],
      regressions: [],
      robustness: {},
    });

    expect(parsed.comparison).toHaveLength(1);
    expect(parsed.comparisonSensitivity.cooldown_5d).toHaveLength(1);
    expect(parsed.comparisonSensitivity.cooldown_20d[0].significant).toBe(
      false
    );
    expect(parsed.comparison[0].unique_dates).toBe(60);
    expect(isConfirmedComparison(parsed.comparison[0])).toBe(true);
    expect(isConfirmedComparison({ ...base, significant: false })).toBe(false);
    expect(isConfirmedComparison({ ...base, ci_low: -0.001 })).toBe(false);
    expect(isConfirmedComparison({ ...base, q_value: null })).toBe(false);
  });
});

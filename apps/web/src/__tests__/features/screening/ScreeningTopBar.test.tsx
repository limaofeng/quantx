import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ScreeningTopBar } from '@/features/screening/components/ScreeningTopBar';

function renderTopBar({
  mode = 'DAILY',
  latestRunStatus = 'success',
  isComplete = false,
  snapshotBackfillLoading = false,
}: {
  mode?: 'DAILY' | 'INTRADAY';
  latestRunStatus?: string;
  isComplete?: boolean;
  snapshotBackfillLoading?: boolean;
} = {}) {
  const onBackfillSnapshot = vi.fn();
  const onOpenAdvancedData = vi.fn();
  render(
    <ScreeningTopBar
      screeningCriteria={{ screeningMode: mode }}
      setScreeningCriteria={vi.fn()}
      availableIndustries={[]}
      meta={{
        total: 0,
        snapshotDate: '2026-07-22',
        expectedSnapshotDate: '2026-07-29',
        missingSnapshotDates: isComplete
          ? []
          : [
              '2026-07-23',
              '2026-07-24',
              '2026-07-27',
              '2026-07-28',
              '2026-07-29',
            ],
        latestRunStatus,
        hasStaleData: !isComplete,
        isComplete,
        warnings: [],
      }}
      onRunScreening={vi.fn()}
      screeningLoading={false}
      onReset={vi.fn()}
      onBackfillSnapshot={onBackfillSnapshot}
      onOpenAdvancedData={onOpenAdvancedData}
      onOpenSnapshotRun={vi.fn()}
      snapshotBackfillLoading={snapshotBackfillLoading}
      snapshotRunState={snapshotBackfillLoading ? 'RUNNING' : null}
    />
  );
  return { onBackfillSnapshot, onOpenAdvancedData };
}

describe('ScreeningTopBar snapshot recovery', () => {
  it('announces missing trading days and exposes both recovery levels', () => {
    const callbacks = renderTopBar();

    expect(screen.getByText('缺少 5 个交易日')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '立即补算' }));
    fireEvent.click(screen.getByRole('button', { name: '高级补数' }));
    expect(callbacks.onBackfillSnapshot).toHaveBeenCalledOnce();
    expect(callbacks.onOpenAdvancedData).toHaveBeenCalledOnce();
  });

  it('disables recovery while the run is active', () => {
    renderTopBar({ snapshotBackfillLoading: true });

    expect(screen.getByRole('button', { name: '补算中' })).toBeDisabled();
    expect(screen.getByText('Prefect · RUNNING')).toBeInTheDocument();
  });

  it('hides daily backfill controls in intraday mode', () => {
    renderTopBar({ mode: 'INTRADAY' });

    expect(
      screen.queryByRole('button', { name: '立即补算' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '高级补数' })
    ).not.toBeInTheDocument();
  });
});

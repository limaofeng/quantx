import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ScreeningResults } from '@/features/screening/components/ScreeningResults';
import { ScreeningTopBar } from '@/features/screening/components/ScreeningTopBar';
import { type ScreeningCriteria } from '@/features/screening/types';

vi.mock('@/features/watchlist/hooks', () => ({
  useWatchlistWorkspace: () => ({
    saveItem: vi.fn().mockResolvedValue({ success: true, message: 'ok' }),
  }),
}));

function renderTopBar({
  mode = 'DAILY',
  latestRunStatus = 'success',
  isComplete = false,
  snapshotBackfillLoading = false,
  hasPendingChanges = false,
}: {
  mode?: 'DAILY' | 'INTRADAY';
  latestRunStatus?: string;
  isComplete?: boolean;
  snapshotBackfillLoading?: boolean;
  hasPendingChanges?: boolean;
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
      hasPendingChanges={hasPendingChanges}
    />
  );
  return { onBackfillSnapshot, onOpenAdvancedData };
}

describe('ScreeningTopBar snapshot recovery', () => {
  it('announces missing trading days and exposes both recovery levels', () => {
    const callbacks = renderTopBar();

    expect(screen.getByText('缺少 5 个交易日')).toBeInTheDocument();
    expect(
      screen.getByText('已应用 0 条条件（全部为 AND）')
    ).toBeInTheDocument();
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

  it('shows only real intraday controls and a pending-change cue', () => {
    renderTopBar({ mode: 'INTRADAY', hasPendingChanges: true });

    expect(screen.getByLabelText('量速')).toBeInTheDocument();
    expect(screen.getByLabelText('额速')).toBeInTheDocument();
    expect(screen.getByLabelText('近 5 分钟放量')).toBeInTheDocument();
    expect(screen.getByLabelText('盘中换手')).toBeInTheDocument();
    expect(screen.getByLabelText('买盘失衡')).toBeInTheDocument();
    expect(screen.queryByLabelText('最小 ROE（TTM）')).not.toBeInTheDocument();
    expect(screen.queryByText('排除 ST')).not.toBeInTheDocument();
    expect(
      screen.getByText('有未应用更改，点击运行后更新结果')
    ).toBeInTheDocument();
    expect(screen.getByText('当前草稿 · 0 条，运行后应用')).toBeInTheDocument();
  });

  it('keeps result columns on the active mode until the draft is run', () => {
    function DraftApplyHarness() {
      const [criteria, setCriteria] = useState<ScreeningCriteria>({
        screeningMode: 'DAILY',
      });
      const [activeMode, setActiveMode] = useState<'DAILY' | 'INTRADAY'>(
        'DAILY'
      );

      return (
        <div>
          <ScreeningTopBar
            screeningCriteria={criteria}
            setScreeningCriteria={setCriteria}
            availableIndustries={[]}
            meta={{
              total: 0,
              snapshotDate: '2026-07-22',
              expectedSnapshotDate: '2026-07-29',
              missingSnapshotDates: [],
              hasStaleData: false,
              isComplete: true,
              warnings: [],
            }}
            onRunScreening={() =>
              setActiveMode(criteria.screeningMode ?? 'DAILY')
            }
            screeningLoading={false}
            onReset={vi.fn()}
            onBackfillSnapshot={vi.fn()}
            onOpenAdvancedData={vi.fn()}
            snapshotBackfillLoading={false}
            hasPendingChanges={
              activeMode !== (criteria.screeningMode ?? 'DAILY')
            }
          />
          <ScreeningResults
            activeMode={activeMode}
            screeningLoading={false}
            results={[]}
            meta={{
              total: 0,
              missingSnapshotDates: [],
              hasStaleData: false,
              isComplete: true,
              warnings: [],
            }}
          />
        </div>
      );
    }

    render(<DraftApplyHarness />);
    expect(screen.getByText('KDJ (9,3,3)')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('screening-mode-INTRADAY'));
    expect(
      screen.getByText('有未应用更改，点击运行后更新结果')
    ).toBeInTheDocument();
    expect(screen.getByText('KDJ (9,3,3)')).toBeInTheDocument();
    expect(
      screen
        .getAllByRole('columnheader')
        .map(header => header.textContent)
        .join('|')
    ).not.toContain('量速');

    fireEvent.click(screen.getByRole('button', { name: '开始盘中扫描' }));
    expect(
      screen
        .getAllByRole('columnheader')
        .map(header => header.textContent)
        .join('|')
    ).toContain('量速');
    expect(screen.queryByText('KDJ (9,3,3)')).not.toBeInTheDocument();
  });
});

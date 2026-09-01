import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ScreeningResults } from '@/features/screening/components/ScreeningResults';
import { ScreeningTopBar } from '@/features/screening/components/ScreeningTopBar';
import {
  type ScreeningCriteria,
  type ScreeningMode,
} from '@/features/screening/types';

vi.mock('@/features/watchlist/hooks', () => ({
  useWatchlistWorkspace: () => ({
    items: [],
    saveItem: vi.fn().mockResolvedValue({ success: true, message: 'ok' }),
  }),
}));

function renderTopBar({
  mode = 'INDICATOR',
  latestRunStatus = 'success',
  isComplete = false,
  snapshotBackfillLoading = false,
  hasPendingChanges = false,
  probabilityModels = [],
}: {
  mode?: ScreeningMode;
  latestRunStatus?: string;
  isComplete?: boolean;
  snapshotBackfillLoading?: boolean;
  hasPendingChanges?: boolean;
  probabilityModels?: Array<{
    modelVersion: string;
    stage: 'ACTIVE' | 'SHADOW';
  }>;
} = {}) {
  const onBackfillSnapshot = vi.fn();
  const onOpenAdvancedData = vi.fn();
  render(
    <ScreeningTopBar
      indicators={[]}
      onRetryCatalog={vi.fn()}
      probabilityModels={probabilityModels}
      onOpenIndicatorReport={vi.fn()}
      onOpenJointReport={vi.fn()}
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
  it('does not describe scoped success as full-market readiness', () => {
    renderTopBar({ latestRunStatus: 'scoped_success', isComplete: false });
    expect(
      screen.getByText('最近仅完成指定标的补算，不代表全市场快照就绪。')
    ).toBeInTheDocument();
    expect(screen.getByText('快照未完整就绪')).toBeInTheDocument();
    expect(screen.queryByText('快照已就绪')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '立即补算' })).toBeEnabled();
  });

  it('announces missing trading days and exposes both recovery levels', () => {
    const callbacks = renderTopBar();

    expect(screen.getByText('历史缺口 5 个交易日')).toBeInTheDocument();
    expect(
      screen.getByText('结果对应已应用条件（全部为 AND）')
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '立即补算' }));
    fireEvent.click(screen.getByRole('button', { name: '高级补数' }));
    expect(callbacks.onBackfillSnapshot).toHaveBeenCalledOnce();
    expect(callbacks.onOpenAdvancedData).toHaveBeenCalledOnce();
  });

  it('disables recovery while the run is active', () => {
    renderTopBar({ snapshotBackfillLoading: true });

    expect(screen.getByRole('button', { name: '立即补算' })).toBeDisabled();
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

  it('keeps probability mode fixed to read-only ordinary A-share candidates', () => {
    renderTopBar({
      mode: 'PROBABILITY',
      probabilityModels: [
        { modelVersion: 'active-model', stage: 'ACTIVE' },
        { modelVersion: 'shadow-model', stage: 'SHADOW' },
      ],
    });

    expect(screen.getByText('固定研究范围')).toBeInTheDocument();
    expect(screen.getByText(/仅沪深普通 A 股/)).toBeInTheDocument();
    expect(screen.getByLabelText('最低校准概率')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '候选模型' })).toBeEnabled();
    expect(screen.getByText('A 级 · 全市场排名 1–20')).toBeInTheDocument();
    expect(screen.getByText(/不会创建策略实例或订单/)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '立即补算' })
    ).not.toBeInTheDocument();
  });

  it('shows only real intraday controls and a pending-change cue', () => {
    renderTopBar({ mode: 'INTRADAY', hasPendingChanges: true });

    expect(screen.getByLabelText('量速')).toBeInTheDocument();
    expect(screen.getByLabelText('额速')).toBeInTheDocument();
    expect(screen.getByLabelText('近 5 分钟放量')).toBeInTheDocument();
    expect(screen.getByLabelText('盘中换手（%）')).toBeInTheDocument();
    expect(screen.getByLabelText('买盘失衡')).toBeInTheDocument();
    expect(screen.queryByLabelText('最小 ROE（TTM）')).not.toBeInTheDocument();
    expect(screen.queryByText('排除 ST')).not.toBeInTheDocument();
    expect(
      screen.getByText('有未应用更改；报告对应当前草稿，结果仍为上次筛选。')
    ).toBeInTheDocument();
    expect(screen.getByText('当前草稿 · 应用后更新结果')).toBeInTheDocument();
  });

  it('keeps result columns on the active mode until the draft is run', () => {
    function DraftApplyHarness() {
      const [criteria, setCriteria] = useState<ScreeningCriteria>({
        screeningMode: 'INDICATOR',
      });
      const [activeMode, setActiveMode] = useState<ScreeningMode>('INDICATOR');

      return (
        <div>
          <ScreeningTopBar
            indicators={[]}
            onRetryCatalog={vi.fn()}
            onOpenIndicatorReport={vi.fn()}
            onOpenJointReport={vi.fn()}
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
              setActiveMode(criteria.screeningMode ?? 'INDICATOR')
            }
            screeningLoading={false}
            onReset={vi.fn()}
            onBackfillSnapshot={vi.fn()}
            onOpenAdvancedData={vi.fn()}
            snapshotBackfillLoading={false}
            hasPendingChanges={
              activeMode !== (criteria.screeningMode ?? 'INDICATOR')
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

    fireEvent.click(screen.getByRole('button', { name: '盘中' }));
    expect(
      screen.getByText('有未应用更改；报告对应当前草稿，结果仍为上次筛选。')
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

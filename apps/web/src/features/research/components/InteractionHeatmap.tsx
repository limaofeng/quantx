import { useMemo } from 'react';

import { selectPreferredBenchmark } from '../model';
import type { GroupStatistic } from '../model';

import { ResearchEmptyState, ResearchPanel } from './ResearchSurface';

interface HeatmapCell {
  mean: number | null;
  position: string;
  rvol: string;
  sampleSize: number;
}

const POSITION_ORDER = ['low', 'mid', 'high'];
const POSITION_LABELS: Record<string, string> = {
  high: '高位',
  low: '低位',
  mid: '中位',
};

function numericBinStart(value: string) {
  const match = value.match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : Number.POSITIVE_INFINITY;
}

function aggregateCells(rows: GroupStatistic[]) {
  const groups = new Map<
    string,
    { position: string; rvol: string; weighted: number; sampleSize: number }
  >();
  rows.forEach(row => {
    const position = row.dimensions.price_position_bin;
    const rvol = row.dimensions.rvol_bin;
    if (!position || !rvol || row.mean === null || row.sample_size <= 0) return;
    const key = `${rvol}\u0000${position}`;
    const current = groups.get(key) || {
      position,
      rvol,
      sampleSize: 0,
      weighted: 0,
    };
    current.weighted += row.mean * row.sample_size;
    current.sampleSize += row.sample_size;
    groups.set(key, current);
  });
  return Array.from(groups.values()).map<HeatmapCell>(group => ({
    mean: group.sampleSize > 0 ? group.weighted / group.sampleSize : null,
    position: group.position,
    rvol: group.rvol,
    sampleSize: group.sampleSize,
  }));
}

function heatColor(value: number | null, maxAbs: number) {
  if (value === null || maxAbs <= 0) return 'rgba(100,116,139,.08)';
  const strength = Math.min(Math.abs(value) / maxAbs, 1);
  const alpha = 0.1 + strength * 0.5;
  return value >= 0 ? `rgba(16,185,129,${alpha})` : `rgba(244,63,94,${alpha})`;
}

export function InteractionHeatmap({ rows }: { rows: GroupStatistic[] }) {
  const benchmark = selectPreferredBenchmark(rows);
  const selectedHorizon =
    rows.length === 0
      ? null
      : rows.some(row => row.horizon === 5)
        ? 5
        : Math.min(...rows.map(row => row.horizon));
  const cells = useMemo(
    () =>
      aggregateCells(
        rows.filter(
          row =>
            row.return_kind === 'close_response' &&
            row.benchmark === benchmark &&
            selectedHorizon !== null &&
            row.horizon === selectedHorizon
        )
      ),
    [benchmark, rows, selectedHorizon]
  );
  const rvolBins = Array.from(new Set(cells.map(cell => cell.rvol))).sort(
    (left, right) => numericBinStart(left) - numericBinStart(right)
  );
  const positions = Array.from(new Set(cells.map(cell => cell.position))).sort(
    (left, right) => {
      const leftIndex = POSITION_ORDER.indexOf(left);
      const rightIndex = POSITION_ORDER.indexOf(right);
      return (
        (leftIndex < 0 ? POSITION_ORDER.length : leftIndex) -
        (rightIndex < 0 ? POSITION_ORDER.length : rightIndex)
      );
    }
  );
  const maxAbs = Math.max(0, ...cells.map(cell => Math.abs(cell.mean || 0)));

  return (
    <ResearchPanel
      title="RVOL × 价格位置"
      description={`${selectedHorizon ?? '—'} 日收盘响应，单元格合并事件方向后按样本数加权；绿色为正、红色为负。`}
    >
      {cells.length === 0 ? (
        <ResearchEmptyState
          title="暂无可用交互分组"
          description="当前运行未生成同时包含 RVOL 与价格位置的有效统计。"
        />
      ) : (
        <div className="overflow-x-auto p-3">
          <table className="w-full min-w-[430px] border-separate border-spacing-1 text-center">
            <caption className="sr-only">
              RVOL 与价格位置对后续收益的交互热力表
            </caption>
            <thead>
              <tr>
                <th className="px-2 py-2 text-left text-ui-caption font-black uppercase tracking-wider text-slate-600">
                  RVOL
                </th>
                {positions.map(position => (
                  <th
                    key={position}
                    scope="col"
                    className="px-2 py-2 text-ui-caption font-bold text-slate-500"
                  >
                    {POSITION_LABELS[position] || position}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rvolBins.map(rvol => (
                <tr key={rvol}>
                  <th
                    scope="row"
                    className="whitespace-nowrap px-2 py-2 text-left font-mono text-ui-caption font-bold text-slate-400"
                  >
                    {rvol}
                  </th>
                  {positions.map(position => {
                    const cell = cells.find(
                      item => item.rvol === rvol && item.position === position
                    );
                    return (
                      <td
                        key={position}
                        className="h-14 min-w-24 rounded border border-white/[0.05] px-2"
                        style={{
                          backgroundColor: heatColor(
                            cell?.mean ?? null,
                            maxAbs
                          ),
                        }}
                      >
                        <span className="block font-mono text-ui-label font-black tabular-nums text-slate-100">
                          {cell?.mean === null || cell?.mean === undefined
                            ? '—'
                            : `${(cell.mean * 100).toFixed(2)}%`}
                        </span>
                        <span className="mt-0.5 block font-mono text-ui-micro text-slate-400">
                          n={cell?.sampleSize || 0}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ResearchPanel>
  );
}

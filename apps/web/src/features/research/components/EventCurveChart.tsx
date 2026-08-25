import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { NativeSelect } from '@/components/ui/native-select';

import { selectPreferredBenchmark } from '../model';
import type { EventCurvePoint } from '../model';

import { ResearchEmptyState, ResearchPanel } from './ResearchSurface';

const BENCHMARK_LABELS: Record<EventCurvePoint['benchmark'], string> = {
  absolute: '绝对收益',
  csi300: '相对沪深300',
  market_equal_weight: '相对等权市场',
};

const RETURN_LABELS: Record<EventCurvePoint['return_kind'], string> = {
  close_response: '收盘响应',
  next_open: '次日开盘执行',
};

function percent(value: number | null) {
  return value === null ? '—' : `${(value * 100).toFixed(2)}%`;
}

export function EventCurveChart({ rows }: { rows: EventCurvePoint[] }) {
  const preferredBenchmark = selectPreferredBenchmark(rows);
  const [benchmark, setBenchmark] =
    useState<EventCurvePoint['benchmark']>(preferredBenchmark);
  const [returnKind, setReturnKind] =
    useState<EventCurvePoint['return_kind']>('close_response');
  const chartRows = useMemo(
    () =>
      rows
        .filter(
          row => row.benchmark === benchmark && row.return_kind === returnKind
        )
        .sort((left, right) => left.horizon - right.horizon)
        .map(row => ({
          ciHigh: row.ci_high === null ? null : row.ci_high * 100,
          ciLow: row.ci_low === null ? null : row.ci_low * 100,
          horizon: row.horizon,
          mean: row.mean === null ? null : row.mean * 100,
          sampleSize: row.sample_size,
        })),
    [benchmark, returnKind, rows]
  );

  return (
    <ResearchPanel
      title="事件后收益曲线"
      description="均值及按事件日期聚类 Bootstrap 的置信区间；横轴为事件后的交易日。"
      className="min-w-0"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.05] px-ui-section py-2">
        <div
          className="flex flex-wrap gap-1"
          aria-label="收益口径"
          role="group"
        >
          {(Object.keys(RETURN_LABELS) as EventCurvePoint['return_kind'][]).map(
            value => (
              <button
                key={value}
                type="button"
                aria-pressed={returnKind === value}
                onClick={() => setReturnKind(value)}
                className={`h-7 cursor-pointer rounded px-2.5 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  returnKind === value
                    ? 'bg-blue-500/15 text-blue-200'
                    : 'text-slate-500 hover:bg-white/[0.04] hover:text-slate-300'
                }`}
              >
                {RETURN_LABELS[value]}
              </button>
            )
          )}
        </div>
        <label className="ml-auto flex items-center gap-2 text-ui-caption font-bold text-slate-500">
          基准
          <NativeSelect
            value={benchmark}
            onChange={event =>
              setBenchmark(event.target.value as EventCurvePoint['benchmark'])
            }
            className="h-7 cursor-pointer rounded border border-white/10 bg-[#0b1120] px-2 text-ui-caption text-slate-300 outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            {(
              Object.keys(BENCHMARK_LABELS) as EventCurvePoint['benchmark'][]
            ).map(value => (
              <option key={value} value={value}>
                {BENCHMARK_LABELS[value]}
              </option>
            ))}
          </NativeSelect>
        </label>
      </div>

      {chartRows.length === 0 ? (
        <ResearchEmptyState
          title="当前口径暂无事件曲线"
          description="该基准或收益口径没有足够的有效样本。"
        />
      ) : (
        <div className="p-3">
          <div
            className="h-64 w-full"
            role="img"
            aria-label={`${RETURN_LABELS[returnKind]}、${BENCHMARK_LABELS[benchmark]}事件后收益曲线`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chartRows}
                margin={{ bottom: 4, left: -12, right: 10, top: 8 }}
              >
                <CartesianGrid
                  stroke="rgba(148,163,184,.10)"
                  vertical={false}
                />
                <XAxis
                  dataKey="horizon"
                  stroke="#64748b"
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickLine={false}
                  unit="日"
                />
                <YAxis
                  stroke="#64748b"
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickFormatter={value => `${value}%`}
                  tickLine={false}
                  width={52}
                />
                <Tooltip
                  contentStyle={{
                    background: '#0b1120',
                    border: '1px solid rgba(255,255,255,.1)',
                    borderRadius: 6,
                    color: '#e2e8f0',
                    fontSize: 11,
                  }}
                  formatter={value => {
                    const numeric = Number(value);
                    return Number.isFinite(numeric)
                      ? `${numeric.toFixed(2)}%`
                      : '—';
                  }}
                  labelFormatter={value => `T+${value} 交易日`}
                />
                <Legend wrapperStyle={{ color: '#94a3b8', fontSize: 10 }} />
                <Line
                  dataKey="ciHigh"
                  dot={false}
                  isAnimationActive={false}
                  name="置信区间上界"
                  stroke="#64748b"
                  strokeDasharray="4 4"
                  strokeWidth={1}
                  type="monotone"
                />
                <Line
                  dataKey="ciLow"
                  dot={false}
                  isAnimationActive={false}
                  name="置信区间下界"
                  stroke="#64748b"
                  strokeDasharray="4 4"
                  strokeWidth={1}
                  type="monotone"
                />
                <Line
                  activeDot={{ r: 4 }}
                  dataKey="mean"
                  isAnimationActive={false}
                  name="平均收益"
                  stroke="#f87171"
                  strokeWidth={2}
                  type="monotone"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <table className="sr-only">
            <caption>事件后收益曲线数据</caption>
            <thead>
              <tr>
                <th>交易日</th>
                <th>平均收益</th>
                <th>置信区间下界</th>
                <th>置信区间上界</th>
                <th>样本数</th>
              </tr>
            </thead>
            <tbody>
              {chartRows.map(row => (
                <tr key={row.horizon}>
                  <td>{row.horizon}</td>
                  <td>{percent(row.mean === null ? null : row.mean / 100)}</td>
                  <td>
                    {percent(row.ciLow === null ? null : row.ciLow / 100)}
                  </td>
                  <td>
                    {percent(row.ciHigh === null ? null : row.ciHigh / 100)}
                  </td>
                  <td>{row.sampleSize}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ResearchPanel>
  );
}

import { ShieldCheck } from 'lucide-react';

import { selectPreferredBenchmark } from '../model';
import type { GroupStatistic } from '../model';

import { ResearchEmptyState, ResearchPanel } from './ResearchSurface';

const LABELS: Record<string, string> = {
  cooldown_20d: '冷却期 20 日',
  cooldown_5d: '冷却期 5 日',
  relative_amount_shock: '相对成交额冲击',
};

function summarize(rows: GroupStatistic[]) {
  const benchmark = selectPreferredBenchmark(rows);
  const horizon =
    rows.length === 0
      ? null
      : rows.some(row => row.horizon === 5)
        ? 5
        : Math.min(...rows.map(row => row.horizon));
  const preferred = rows.filter(
    row =>
      row.return_kind === 'close_response' &&
      horizon !== null &&
      row.horizon === horizon &&
      row.benchmark === benchmark
  );
  const sampleSize = preferred.reduce(
    (total, row) => total + row.sample_size,
    0
  );
  const weighted =
    sampleSize > 0
      ? preferred.reduce(
          (total, row) => total + (row.mean || 0) * row.sample_size,
          0
        ) / sampleSize
      : null;
  return {
    cellCount: preferred.length,
    mean: weighted,
    sampleSize,
    significantCells: preferred.filter(row => row.significant).length,
  };
}

export function RobustnessSummary({
  robustness,
}: {
  robustness: Record<string, GroupStatistic[]>;
}) {
  const entries = Object.entries(robustness);
  return (
    <ResearchPanel
      title="稳健性检验"
      description="对事件冷却期与量能代理变量进行替换；摘要优先采用 T+5 收盘响应，缺失时使用最近可用周期。"
    >
      {entries.length === 0 ? (
        <ResearchEmptyState
          title="暂无稳健性结果"
          description="该运行没有生成替代口径或敏感性检验。"
        />
      ) : (
        <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3">
          {entries.map(([name, rows]) => {
            const summary = summarize(rows);
            return (
              <article
                key={name}
                className="rounded-md border border-white/[0.06] bg-white/[0.025] p-3"
              >
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-3.5 w-3.5 text-sky-300" />
                  <h3 className="text-[11px] font-bold text-slate-300">
                    {LABELS[name] || name}
                  </h3>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
                  <div>
                    <dt className="text-[9px] uppercase tracking-wide text-slate-600">
                      加权收益
                    </dt>
                    <dd className="mt-0.5 font-mono text-sm font-bold tabular-nums text-slate-100">
                      {summary.mean === null
                        ? '—'
                        : `${(summary.mean * 100).toFixed(2)}%`}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[9px] uppercase tracking-wide text-slate-600">
                      分组样本
                    </dt>
                    <dd className="mt-0.5 font-mono text-sm font-bold tabular-nums text-slate-100">
                      {summary.sampleSize.toLocaleString()}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[9px] uppercase tracking-wide text-slate-600">
                      有效单元
                    </dt>
                    <dd className="mt-0.5 font-mono text-xs text-slate-400">
                      {summary.cellCount}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[9px] uppercase tracking-wide text-slate-600">
                      显著单元
                    </dt>
                    <dd className="mt-0.5 font-mono text-xs text-slate-400">
                      {summary.significantCells}
                    </dd>
                  </div>
                </dl>
              </article>
            );
          })}
        </div>
      )}
    </ResearchPanel>
  );
}

import { ExternalLink, RotateCcw } from 'lucide-react';

import type { StockDisclosureSummaryQuery as StockDisclosureSummaryData } from '@/generated/gql/graphql';

import {
  formatCompactCurrency,
  formatDate,
  formatPercent,
  formatPrice,
  formatShares,
  getProgressPercent,
  sourceLabel,
} from './formatters';

type DisclosureSummary = NonNullable<
  StockDisclosureSummaryData['stockDisclosureSummary']
>;
type RepurchaseEvent = DisclosureSummary['repurchaseEvents'][0];

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border border-white/5 bg-[#08101d]/80 px-3 py-2">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-xs font-black text-slate-200">
        {value}
      </div>
    </div>
  );
}

export function RepurchaseBrief({
  event,
  sourceStatus,
}: {
  event?: RepurchaseEvent | null;
  sourceStatus?: string | null;
}) {
  const progressPercent = event
    ? getProgressPercent(event.repurchasedAmount, event.plannedAmountUpper)
    : 0;

  return (
    <section className="min-w-0 border border-white/5 bg-[#0b1120]/70">
      <div className="flex h-10 items-center justify-between gap-2 border-b border-white/5 px-3">
        <div className="flex min-w-0 items-center gap-2">
          <RotateCcw className="h-3.5 w-3.5 text-blue-300" />
          <h3 className="truncate text-xs font-black text-slate-200">
            回购观察
          </h3>
        </div>
        {event?.sourceUrl && (
          <a
            href={event.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-blue-500/10 hover:text-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
            aria-label="打开回购来源"
            title="打开回购来源"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      <div className="p-3">
        {!event ? (
          <div className="flex min-h-32 flex-col justify-center gap-2 text-center">
            <div className="text-xs font-bold text-slate-400">暂无回购事件</div>
            <div className="text-[11px] font-medium text-slate-600">
              {sourceStatus === 'READY'
                ? '等待首次同步，可点击刷新公告。'
                : '回购数据来自东方财富股票回购数据。'}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] font-black text-red-200">
                {event.progressStatus || '回购事件'}
              </span>
              <span className="font-mono text-[10px] font-bold text-slate-500">
                {formatDate(event.latestAnnounceDate)}
              </span>
              <span className="text-[10px] font-bold text-slate-600">
                {sourceLabel(event.source)}
              </span>
            </div>

            <div>
              <div className="flex items-center justify-between gap-3 text-[10px] font-bold text-slate-500">
                <span>金额进度</span>
                <span className="font-mono text-slate-300">
                  {progressPercent.toFixed(0)}%
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full bg-red-400"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>

            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <Metric
                label="价格上限"
                value={formatPrice(event.priceCeiling)}
              />
              <Metric
                label="计划金额"
                value={`${formatCompactCurrency(
                  event.plannedAmountLower
                )} - ${formatCompactCurrency(event.plannedAmountUpper)}`}
              />
              <Metric
                label="计划数量"
                value={`${formatShares(event.plannedQuantityLower)} - ${formatShares(
                  event.plannedQuantityUpper
                )} 股`}
              />
              <Metric
                label="已回购金额"
                value={formatCompactCurrency(event.repurchasedAmount)}
              />
              <Metric
                label="已回购数量"
                value={`${formatShares(event.repurchasedQuantity)} 股`}
              />
              <Metric
                label="已回购比例"
                value={formatPercent(event.repurchasedRatio, false)}
              />
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

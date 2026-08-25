import { AlertCircle, BookOpen, Lock } from 'lucide-react';

import { Card } from '@/components/ui/card';

import { formatMoney, formatNumber } from '../formatters';
import type { GridBook, GridBookSummary } from '../types';

interface GridBookSummaryCardProps {
  instrumentCode: string;
  book?: GridBook;
  summary: GridBookSummary;
  editable: boolean;
  backtestId?: string | null;
  saveError?: string | null;
}

function SummaryTile({
  label,
  value,
  className = 'text-slate-700 dark:text-slate-200',
}: {
  label: string;
  value: string | number;
  className?: string;
}) {
  return (
    <div className="rounded-panel border border-slate-200 px-3 py-2 dark:border-white/10">
      <div className="text-ui-micro uppercase tracking-widest text-slate-400">
        {label}
      </div>
      <div className={`mt-1 ${className}`}>{value}</div>
    </div>
  );
}

function InventoryTile({
  label,
  value,
  className,
}: {
  label: string;
  value: string | number;
  className: string;
}) {
  return (
    <div className={`rounded-panel border px-3 py-2 ${className}`}>
      <div className="text-ui-micro uppercase tracking-widest text-slate-400">
        {label}
      </div>
      <div className="mt-1">{value}</div>
    </div>
  );
}

export function GridBookSummaryCard({
  instrumentCode,
  book,
  summary,
  editable,
  backtestId,
  saveError,
}: GridBookSummaryCardProps) {
  const viewLabel = backtestId ? '回测快照' : '模板版本';

  return (
    <Card className="rounded-panel border border-slate-200 bg-white p-ui-panel shadow-none dark:border-white/10 dark:bg-slate-900/60">
      <div className="flex flex-col gap-ui-section lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-ui-micro font-black uppercase tracking-[0.3em] text-red-500">
            <BookOpen className="h-4 w-4" />
            网格簿
            <span className="rounded-md border border-red-500/20 bg-red-500/10 px-2 py-0.5 tracking-normal text-red-300">
              {viewLabel}
            </span>
          </div>
          <h3 className="mt-2 text-ui-heading font-black text-slate-900 dark:text-white">
            {book?.instrumentCode || instrumentCode}
          </h3>
          <p className="mt-1 text-ui-label font-medium text-slate-500">
            网格簿只维护未来执行计划；成交状态只能由 broker / miniQMT 回报推进。
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 text-ui-caption font-bold text-slate-500 md:grid-cols-6">
          <SummaryTile label="总档位" value={summary.totalLevels} />
          <SummaryTile
            label="启用"
            value={summary.enabledLevels}
            className="text-emerald-500"
          />
          <SummaryTile
            label="待成交"
            value={summary.pendingLevels}
            className="text-amber-500"
          />
          <SummaryTile
            label="已成交"
            value={summary.filledLevels}
            className="text-red-500"
          />
          <SummaryTile
            label="禁用"
            value={summary.disabledLevels}
            className="text-slate-500"
          />
          <SummaryTile
            label="计划资金"
            value={formatMoney(summary.plannedAmount)}
          />
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 text-ui-caption font-bold text-slate-500 md:grid-cols-6">
        <InventoryTile
          label="买入档"
          value={summary.buySlotCount || 0}
          className="border-market-up/20 bg-market-up/5 text-market-up"
        />
        <InventoryTile
          label="卖出档"
          value={summary.sellWaterlineCount || 0}
          className="border-market-down/20 bg-market-down/5 text-market-down"
        />
        <InventoryTile
          label="可用库存"
          value={formatNumber(summary.openLotShares || 0)}
          className="border-emerald-500/20 bg-emerald-500/5 text-emerald-400"
        />
        <InventoryTile
          label="已预留"
          value={formatNumber(summary.reservedLotShares || 0)}
          className="border-amber-500/20 bg-amber-500/5 text-amber-400"
        />
        <InventoryTile
          label="等待库存"
          value={summary.waitingInventoryLevels || 0}
          className="border-purple-500/20 bg-purple-500/5 text-purple-300"
        />
        <SummaryTile label="循环次数" value={summary.completedCycles || 0} />
      </div>

      {!editable && (
        <div className="mt-5 flex items-start gap-2 rounded-panel border border-amber-500/20 bg-amber-500/10 px-ui-section py-3 text-ui-label font-medium text-amber-300">
          <Lock className="mt-0.5 h-4 w-4 shrink-0" />
          {backtestId
            ? '已完成回测版本是只读快照；请回到模板版本修改网格簿后重新回测。'
            : '运行中不可维护网格簿，请先暂停实例。当前页面只读。'}
        </div>
      )}

      {book?.needsBacktest && (
        <div className="mt-5 rounded-panel border border-red-500/20 bg-red-500/10 px-ui-section py-3 text-ui-label font-medium text-red-300">
          网格簿计划已变更，当前回测结果可能不是最新计划，请重新回测。
        </div>
      )}

      {saveError && (
        <div className="mt-5 flex items-start gap-2 rounded-panel border border-rose-500/20 bg-rose-500/10 px-ui-section py-3 text-ui-label font-medium text-rose-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {saveError}
        </div>
      )}
    </Card>
  );
}

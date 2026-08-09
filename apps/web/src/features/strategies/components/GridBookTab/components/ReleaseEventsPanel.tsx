import { Card } from '@/components/ui/card';

import { formatNumber } from '../formatters';
import type { GridReleaseEvent } from '../types';

export function ReleaseEventsPanel({
  events,
}: {
  events?: GridReleaseEvent[];
}) {
  return (
    <Card className="overflow-hidden rounded-[2rem] border border-slate-200 bg-white shadow-xl dark:border-white/10 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-6 py-5 dark:border-white/5">
        <div className="text-[10px] font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
          释放记录
        </div>
        <p className="mt-1 text-[10px] font-medium text-slate-500">
          卖出成交后扣减库存批次，并按最近下方规则释放买入档。
        </p>
      </div>
      <div className="divide-y divide-slate-100 dark:divide-white/5">
        {(events || []).length === 0 ? (
          <div className="px-6 py-10 text-center text-xs font-bold text-slate-400">
            暂无释放记录。
          </div>
        ) : (
          (events || [])
            .slice(-20)
            .reverse()
            .map(event => (
              <div
                key={event.eventId}
                className="grid grid-cols-12 gap-3 px-6 py-4 text-[11px] font-bold"
              >
                <div className="col-span-4 min-w-0">
                  <div className="truncate font-mono text-slate-700 dark:text-slate-200">
                    卖出 #{event.sellLevelIndex ?? '--'}
                  </div>
                  <div className="mt-1 truncate text-[9px] text-slate-400">
                    释放 #{event.releasedLevelIndex ?? '--'} ·{' '}
                    {event.lotIds.join(', ') || '--'}
                  </div>
                </div>
                <div className="col-span-2 text-right font-mono text-slate-500">
                  {formatNumber(event.price)}
                </div>
                <div className="col-span-2 text-right font-mono text-slate-500">
                  {formatNumber(event.shares)}
                </div>
                <div className="col-span-4 min-w-0 text-right">
                  <div className="truncate font-mono text-slate-500">
                    {event.orderId || event.intentId || event.tradeId || '--'}
                  </div>
                  <div className="mt-1 text-[9px] text-slate-400">
                    {event.createdAt || '--'}
                  </div>
                </div>
              </div>
            ))
        )}
      </div>
    </Card>
  );
}

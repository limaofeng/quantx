import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';

import {
  bucketLabel,
  formatNumber,
  inventorySourceLabel,
  inventoryStatusLabel,
  statusClass,
} from '../formatters';
import type { GridInventoryLot } from '../types';

export function InventoryLotsPanel({ lots }: { lots?: GridInventoryLot[] }) {
  return (
    <Card className="overflow-hidden rounded-panel border border-slate-200 bg-white shadow-none dark:border-white/10 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-ui-panel py-ui-section dark:border-white/5">
        <div className="text-ui-caption font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
          活跃库存池
        </div>
        <p className="mt-1 text-ui-caption font-medium text-slate-500">
          买入成交和初始活跃仓形成库存批次，卖出档只能从这里提取。
        </p>
      </div>
      <div className="divide-y divide-slate-100 dark:divide-white/5">
        {(lots || []).length === 0 ? (
          <div className="px-ui-panel py-ui-empty text-center text-ui-label font-bold text-slate-400">
            暂无活跃库存 lot。
          </div>
        ) : (
          (lots || []).map(lot => (
            <div
              key={lot.lotId}
              className="grid grid-cols-12 gap-3 px-ui-panel py-ui-section text-ui-caption font-bold"
            >
              <div className="col-span-3 min-w-0">
                <div className="truncate font-mono text-slate-700 dark:text-slate-200">
                  {lot.lotId}
                </div>
                <div className="mt-1 text-ui-micro text-slate-400">
                  {inventorySourceLabel(lot.source)} · {bucketLabel(lot.bucket)}
                </div>
              </div>
              <div className="col-span-2 text-right font-mono text-slate-500">
                {formatNumber(lot.entryPrice)}
              </div>
              <div className="col-span-3 text-right font-mono text-slate-500">
                {formatNumber(lot.remainingShares)} /{' '}
                {formatNumber(lot.originalShares)}
              </div>
              <div className="col-span-2 text-right font-mono text-amber-400">
                {formatNumber(lot.reservedShares)}
              </div>
              <div className="col-span-2 text-right">
                <Badge variant="outline" className={statusClass(lot.status)}>
                  {inventoryStatusLabel(lot.status)}
                </Badge>
              </div>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}

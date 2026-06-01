import { Archive, Boxes, Layers3 } from 'lucide-react';

import { Card } from '@/components/ui/card';

import type { BucketLedgerView, StrategyInstance } from '../domain';

interface BucketLedgerTabProps {
  instance?: StrategyInstance | null;
  ledger: BucketLedgerView;
}

function BucketTile({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: typeof Archive;
  tone: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-white/10 dark:bg-white/[0.03]">
      <div className="mb-4 flex items-center justify-between">
        <div className={`rounded-xl p-2.5 ${tone}`}>
          <Icon className="h-4 w-4" />
        </div>
        <span className="text-[9px] font-black uppercase tracking-[0.2em] text-slate-400">
          Bucket
        </span>
      </div>
      <div className="text-[10px] font-black uppercase tracking-[0.24em] text-slate-500">
        {label}
      </div>
      <div className="mt-2 font-mono text-2xl font-black text-slate-900 dark:text-white">
        {value}
      </div>
    </div>
  );
}

export default function BucketLedgerTab({
  instance,
  ledger,
}: BucketLedgerTabProps) {
  if (!instance) {
    return (
      <Card className="p-10 text-center">
        <p className="text-sm font-bold text-slate-500">请先选择策略实例。</p>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl dark:border-white/10 dark:bg-slate-900/60">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-[9px] font-black uppercase tracking-[0.3em] text-blue-500">
              仓位归因
            </div>
            <h3 className="mt-1 text-lg font-black text-slate-900 dark:text-white">
              {instance.instrumentCode}
            </h3>
          </div>
          <p className="max-w-lg text-xs font-medium leading-relaxed text-slate-500">
            展示使用用户语义：封存仓、核心仓、活跃仓。`locked_core`
            默认不能方向性卖出，任何库存置换都应进入审计。
          </p>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <BucketTile
          label="封存仓"
          value={ledger.lockedCore}
          icon={Archive}
          tone="bg-slate-500/10 text-slate-500"
        />
        <BucketTile
          label="核心仓"
          value={ledger.core}
          icon={Layers3}
          tone="bg-blue-500/10 text-blue-500"
        />
        <BucketTile
          label="活跃仓"
          value={ledger.swing}
          icon={Boxes}
          tone="bg-emerald-500/10 text-emerald-500"
        />
      </div>

      <Card className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-xl dark:border-white/10 dark:bg-slate-900/60">
        <div className="flex items-center justify-between">
          <span className="text-[9px] font-black uppercase tracking-[0.24em] text-slate-400">
            更新时间
          </span>
          <span className="font-mono text-xs font-bold text-slate-600 dark:text-slate-300">
            {ledger.updatedAt
              ? new Date(ledger.updatedAt).toLocaleString('zh-CN')
              : '后端暂未返回'}
          </span>
        </div>
      </Card>
    </div>
  );
}

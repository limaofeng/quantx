import { ClipboardList } from 'lucide-react';

import { Card } from '@/components/ui/card';

interface TradingIntentsTabProps {
  strategyId: string;
}

export default function TradingIntentsTab({
  strategyId: _strategyId,
}: TradingIntentsTabProps) {
  return (
    <Card className="rounded-[2rem] border border-dashed border-slate-200 bg-white p-12 text-center shadow-xl dark:border-white/10 dark:bg-slate-900/60">
      <ClipboardList className="mx-auto mb-5 h-10 w-10 text-slate-300" />
      <h3 className="mb-2 text-sm font-black uppercase tracking-[0.2em] text-slate-700 dark:text-slate-200">
        策略意图已迁移
      </h3>
      <p className="mx-auto max-w-lg text-xs font-medium leading-relaxed text-slate-500">
        请在“决策审计”中查看策略意图，在“执行跟踪”中查看风控、委托和成交状态。
        这个兼容组件不再展示旧的交易生命周期 mock 数据。
      </p>
    </Card>
  );
}

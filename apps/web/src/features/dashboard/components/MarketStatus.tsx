// 市场状态组件
import { Card } from '@/components/ui/card';

export function MarketStatus() {
  // Temporary placeholder data until real API is connected
  const marketIndices = [
    { name: '上证指数', value: '3,050.12', change: '+1.2%', isPositive: true },
    { name: '深证成指', value: '9,850.55', change: '+0.8%', isPositive: true },
    { name: '创业板指', value: '1,950.33', change: '-0.5%', isPositive: false },
  ];

  return (
    <Card className="rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-black text-slate-200">市场状态</h3>
          <p className="mt-1 text-[11px] text-slate-400">主要指数行情快照</p>
        </div>
        <span className="shrink-0 text-[11px] font-medium text-slate-400">
          指数概览
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {marketIndices.map(index => (
          <div
            key={index.name}
            className="rounded-md border border-white/[0.06] bg-white/[0.025] px-3 py-2.5"
          >
            <span className="block text-[11px] font-medium text-slate-400">
              {index.name}
            </span>
            <div className="mt-1.5 flex items-baseline justify-between gap-2">
              <span
                className="font-mono text-sm font-bold tabular-nums text-slate-100"
                data-testid={`market-${index.name}-value`}
              >
                {index.value}
              </span>
              <span
                className={`shrink-0 text-xs font-bold tabular-nums ${index.isPositive ? 'text-success' : 'text-destructive'}`}
                data-testid={`market-${index.name}-change`}
              >
                {index.change}
              </span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

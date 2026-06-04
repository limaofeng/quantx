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
      <h3 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
        市场状态
      </h3>
      <div className="space-y-2">
        {marketIndices.map(index => (
          <div
            key={index.name}
            className="flex items-center justify-between border-b border-white/5 py-1.5 last:border-b-0"
          >
            <span className="text-xs font-medium text-slate-500">
              {index.name}
            </span>
            <div className="text-right">
              <span
                className="font-mono text-xs font-bold text-slate-200"
                data-testid={`market-${index.name}-value`}
              >
                {index.value}
              </span>
              <span
                className={`ml-2 text-xs font-bold ${index.isPositive ? 'text-success' : 'text-destructive'}`}
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

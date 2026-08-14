// 主要持仓组件
import { ArrowRight } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import type { EnrichedHolding } from '@/shared/types';
import { financialToneClass } from '@/shared/utils/financialColors';
import {
  getStockIconText,
  formatCurrency,
  formatPercent,
} from '@/shared/utils/format';

interface TopHoldingsProps {
  holdings: EnrichedHolding[];
  isLoading: boolean;
}

export function TopHoldings({ holdings, isLoading }: TopHoldingsProps) {
  if (isLoading) {
    return (
      <Card className="rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
        <h3 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
          主要持仓
        </h3>
        <div className="text-sm text-slate-400">加载持仓数据中...</div>
      </Card>
    );
  }

  if (holdings.length === 0) {
    return (
      <Card className="rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
        <h3 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
          主要持仓
        </h3>
        <div className="py-8 text-center text-sm text-slate-400">
          暂无持仓数据
        </div>
      </Card>
    );
  }

  return (
    <Card className="rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
      <h3 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
        主要持仓
      </h3>
      <div className="space-y-2">
        {holdings.map(holding => {
          const stock = holding.stock;
          if (!stock) return null;

          const currentPrice =
            typeof stock.currentPrice === 'number'
              ? stock.currentPrice
              : Number(stock.currentPrice || 0);
          const currentValue = holding.volume * currentPrice;
          const cost = holding.volume * holding.avgPrice;
          const pnl = currentValue - cost;
          const pnlPercent =
            cost > 0 ? ((currentValue - cost) / cost) * 100 : 0;

          return (
            <div
              key={holding.id}
              className="flex items-center justify-between border-b border-white/5 py-2 last:border-b-0"
              data-testid={`holding-${stock.code}`}
            >
              <div className="flex items-center">
                <div className="mr-3 flex h-9 w-9 items-center justify-center rounded-md border border-red-500/10 bg-red-500/10">
                  <span className="text-sm font-bold text-red-300">
                    {getStockIconText(stock.name)}
                  </span>
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-200">
                    {stock.name} ({stock.code})
                  </p>
                  <p className="text-xs text-slate-400">
                    {holding.volume}股 • 成本{formatCurrency(holding.avgPrice)}
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p
                  className="font-mono text-sm font-medium text-slate-200"
                  data-testid={`holding-${stock.code}-value`}
                >
                  {formatCurrency(currentValue)}
                </p>
                <p
                  className={`text-sm ${financialToneClass(pnl, 'holding')}`}
                >
                  {formatPercent(pnlPercent)} ({pnl >= 0 ? '+' : ''}
                  {formatCurrency(pnl)})
                </p>
              </div>
            </div>
          );
        })}
      </div>
      <Link href="/holdings">
        <Button
          variant="ghost"
          className="mt-3 h-8 w-full text-xs text-red-300 hover:text-red-100"
          data-testid="view-all-holdings"
        >
          查看全部持仓 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
      </Link>
    </Card>
  );
}

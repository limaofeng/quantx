// 主要持仓组件
import { ArrowRight } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import type { EnrichedHolding } from '@/shared/types';
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
      <Card className="p-6">
        <h3 className="text-lg font-semibold mb-4">主要持仓</h3>
        <div>加载持仓数据中...</div>
      </Card>
    );
  }

  if (holdings.length === 0) {
    return (
      <Card className="p-6">
        <h3 className="text-lg font-semibold mb-4">主要持仓</h3>
        <div className="text-center py-8 text-muted-foreground">
          暂无持仓数据
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">主要持仓</h3>
      <div className="space-y-4">
        {holdings.map(holding => {
          const stock = holding.stock;
          if (!stock) return null;

          const currentValue = holding.volume * (stock.currentPrice || 0);
          const cost = holding.volume * holding.avgPrice;
          const pnl = currentValue - cost;
          const pnlPercent =
            cost > 0 ? ((currentValue - cost) / cost) * 100 : 0;

          return (
            <div
              key={holding.id}
              className="flex items-center justify-between py-3 border-b border-border last:border-b-0"
              data-testid={`holding-${stock.code}`}
            >
              <div className="flex items-center">
                <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center mr-3">
                  <span className="text-primary font-medium">
                    {getStockIconText(stock.name)}
                  </span>
                </div>
                <div>
                  <p className="font-medium">
                    {stock.name} ({stock.code})
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {holding.volume}股 • 成本{formatCurrency(holding.avgPrice)}
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p
                  className="font-medium"
                  data-testid={`holding-${stock.code}-value`}
                >
                  {formatCurrency(currentValue)}
                </p>
                <p
                  className={`text-sm ${pnl >= 0 ? 'text-success' : 'text-destructive'}`}
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
          className="w-full mt-4 text-primary hover:text-primary/80"
          data-testid="view-all-holdings"
        >
          查看全部持仓 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
      </Link>
    </Card>
  );
}

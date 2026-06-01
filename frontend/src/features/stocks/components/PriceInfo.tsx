import { ArrowDownRight, ArrowUpRight } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { formatPercent } from '@/shared/utils';

import { type StockDetail } from '../types';

type PriceInfoProps = {
  stock: StockDetail;
};

export default function PriceInfo({ stock }: PriceInfoProps) {
  const quote = stock.quote!;
  const isPositive = quote.change! >= 0;
  const changePercent = quote.changePercent!;
  return (
    <Card className="p-6 mb-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div>
          <p className="text-sm text-muted-foreground mb-1">当前价格</p>
          <div className="flex items-baseline">
            <span className="text-3xl font-bold" data-testid="current-price">
              ¥{quote.lastPrice.toFixed(2)}
            </span>
            <div
              className={`ml-3 flex items-center ${isPositive ? 'text-success' : 'text-destructive'}`}
            >
              {isPositive ? (
                <ArrowUpRight className="h-4 w-4 mr-1" />
              ) : (
                <ArrowDownRight className="h-4 w-4 mr-1" />
              )}
              <span data-testid="price-change">
                {formatPercent(changePercent)}
              </span>
            </div>
          </div>
        </div>

        <div>
          <p className="text-sm text-muted-foreground mb-1">52周最高/最低</p>
          <div className="space-y-1">
            <div className="flex justify-between">
              <span className="text-sm">最高:</span>
              <span
                className="font-medium text-destructive"
                data-testid="52w-high"
              >
                ¥{quote.high.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm">最低:</span>
              <span className="font-medium text-success" data-testid="52w-low">
                ¥{quote.low.toFixed(2)}
              </span>
            </div>
          </div>
        </div>

        <div>
          <p className="text-sm text-muted-foreground mb-1">成交量/成交额</p>
          <div className="space-y-1">
            <div className="flex justify-between">
              <span className="text-sm">成交量:</span>
              <span className="font-medium" data-testid="volume">
                {quote.volume}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm">成交额:</span>
              <span className="font-medium" data-testid="turnover">
                {quote.amount}
              </span>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

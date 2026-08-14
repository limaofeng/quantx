import { ArrowDownRight, ArrowUpRight } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { formatPercent } from '@/shared/utils';
import { financialToneClass } from '@/shared/utils/financialColors';

import { type StockDetail } from '../types';

type PriceInfoProps = {
  stock: StockDetail;
};

export default function PriceInfo({ stock }: PriceInfoProps) {
  const quote = stock.quote!;
  const isPositive = quote.change! >= 0;
  const changePercent = quote.changePercent!;
  return (
    <Card className="mb-0 rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
      <div className="grid grid-cols-3 gap-4">
        <div>
          <p className="mb-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
            当前价格
          </p>
          <div className="flex items-baseline">
            <span
              className="font-mono text-2xl font-black text-slate-100"
              data-testid="current-price"
            >
              ¥{quote.lastPrice.toFixed(2)}
            </span>
            <div
              className={`ml-3 flex items-center text-xs font-bold ${financialToneClass(quote.change)}`}
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
          <p className="mb-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
            52周最高/最低
          </p>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-500">最高:</span>
              <span
                className="font-mono font-medium text-market-up"
                data-testid="52w-high"
              >
                ¥{quote.high.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">最低:</span>
              <span
                className="font-mono font-medium text-market-down"
                data-testid="52w-low"
              >
                ¥{quote.low.toFixed(2)}
              </span>
            </div>
          </div>
        </div>

        <div>
          <p className="mb-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
            成交量/成交额
          </p>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-500">成交量:</span>
              <span
                className="font-mono font-medium text-slate-200"
                data-testid="volume"
              >
                {quote.volume}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">成交额:</span>
              <span
                className="font-mono font-medium text-slate-200"
                data-testid="turnover"
              >
                {quote.amount}
              </span>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

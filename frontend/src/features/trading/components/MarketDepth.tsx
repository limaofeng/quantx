import { Card } from '@/components/ui/card';

interface MarketDepthProps {
  selectedStock: any | null;
  onPriceSelect?: (price: string) => void;
}

export function MarketDepth({
  selectedStock,
  onPriceSelect,
}: MarketDepthProps) {
  // 演示数据备选项
  const demoStock = {
    name: '平安银行 (示例)',
    id: '000001',
    currentPrice: '12.45',
    quote: { lastPrice: 12.45, changePercent: 1.2 },
  };

  const activeStock = selectedStock || demoStock;
  const isDemo = !selectedStock;

  // 获取基础价格，处理不同版本的数据结构
  const basePrice =
    activeStock.quote?.lastPrice ?? parseFloat(activeStock.currentPrice ?? '0');

  return (
    <Card
      square
      className="card-elevated p-3 h-full flex flex-col border-none shadow-none bg-slate-50/80 dark:bg-slate-950/80 overflow-hidden px-1 animate-fade-in group"
    >
      <div className="flex items-center justify-between mb-3 px-2">
        <div className="flex items-center gap-2">
          <h4 className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/80">
            五档行情
          </h4>
          {isDemo && (
            <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/10 text-primary rounded-full font-bold border border-blue-500/20">
              演示
            </span>
          )}
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[11px] font-mono font-bold text-foreground/90">
            {activeStock.name}
          </span>
          <span className="text-[9px] font-mono text-muted-foreground opacity-60">
            {activeStock.id || activeStock.stockCode}
          </span>
        </div>
      </div>

      <div className="flex-1 flex flex-col overflow-auto font-mono text-xs custom-scrollbar">
        {/* Sell Orders (Top) - Reversed 5 -> 1 */}
        <div className="flex flex-col gap-[1px] mb-1">
          {(() => {
            const sellOrders = [
              {
                level: 5,
                price: basePrice + 0.05,
                volume: Math.floor(Math.random() * 50000) + 1200,
              },
              {
                level: 4,
                price: basePrice + 0.04,
                volume: Math.floor(Math.random() * 80000) + 2500,
              },
              {
                level: 3,
                price: basePrice + 0.03,
                volume: Math.floor(Math.random() * 120000) + 4000,
              },
              {
                level: 2,
                price: basePrice + 0.02,
                volume: Math.floor(Math.random() * 150000) + 6000,
              },
              {
                level: 1,
                price: basePrice + 0.01,
                volume: Math.floor(Math.random() * 200000) + 9000,
              },
            ];
            const maxVol = Math.max(...sellOrders.map(o => o.volume));

            return sellOrders.map(order => (
              <div
                key={`sell-${order.level}`}
                className="relative flex justify-between items-center h-5.5 hover:bg-success/5 px-2 group cursor-pointer transition-colors duration-200"
                onClick={() => onPriceSelect?.(order.price.toFixed(2))}
              >
                <div
                  className="absolute right-0 h-[80%] my-auto bg-gradient-to-l from-emerald-500/20 to-transparent transition-all duration-700 rounded-l-sm"
                  style={{ width: `${(order.volume / maxVol) * 70}%` }}
                />

                <div className="flex items-center gap-2 z-10 w-1/4">
                  <span className="text-[10px] text-muted-foreground/60 w-6 text-left font-medium">
                    卖{order.level}
                  </span>
                </div>

                <span className="text-success font-bold z-10 w-1/3 text-right tabular-nums">
                  {order.price.toFixed(2)}
                </span>

                <span className="text-[10px] font-medium text-muted-foreground/80 z-10 w-1/3 text-right tabular-nums">
                  {(order.volume / 100).toFixed(0)}
                </span>
              </div>
            ));
          })()}
        </div>

        {/* Current Price Bar (Middle) */}
        <div className="flex items-center justify-between px-3 h-9 my-1 bg-slate-100/20 dark:bg-slate-900/20 border-y border-slate-200/20 dark:border-slate-800/20 shadow-inner overflow-hidden animate-price-flash">
          <div className="flex items-baseline gap-2">
            <span className="text-base font-black font-mono text-destructive tracking-tight drop-shadow-sm">
              {basePrice.toFixed(2)}
            </span>
            <span className="text-[10px] font-bold font-mono text-destructive/90 bg-rose-500/10 px-1 py-0.5 rounded leading-none">
              +{(Math.random() * 0.5 + 0.1).toFixed(2)}%
            </span>
          </div>
          <div className="flex flex-col items-end leading-none">
            <span className="text-[9px] text-muted-foreground/60 font-bold uppercase tracking-tighter">
              最新价
            </span>
            <div className="w-1 h-1 bg-destructive rounded-full mt-0.5 animate-pulse" />
          </div>
        </div>

        {/* Buy Orders (Bottom) - 1 -> 5 */}
        <div className="flex flex-col gap-[1px]">
          {(() => {
            const buyOrders = [
              {
                level: 1,
                price: basePrice - 0.01,
                volume: Math.floor(Math.random() * 250000) + 15000,
              },
              {
                level: 2,
                price: basePrice - 0.02,
                volume: Math.floor(Math.random() * 180000) + 8000,
              },
              {
                level: 3,
                price: basePrice - 0.03,
                volume: Math.floor(Math.random() * 150000) + 5000,
              },
              {
                level: 4,
                price: basePrice - 0.04,
                volume: Math.floor(Math.random() * 100000) + 3000,
              },
              {
                level: 5,
                price: basePrice - 0.05,
                volume: Math.floor(Math.random() * 80000) + 2000,
              },
            ];
            const maxVol = Math.max(...buyOrders.map(o => o.volume));

            return buyOrders.map(order => (
              <div
                key={`buy-${order.level}`}
                className="relative flex justify-between items-center h-5.5 hover:bg-destructive/5 px-2 group cursor-pointer transition-colors duration-200"
                onClick={() => onPriceSelect?.(order.price.toFixed(2))}
              >
                <div
                  className="absolute right-0 h-[80%] my-auto bg-gradient-to-l from-rose-500/20 to-transparent transition-all duration-700 rounded-l-sm"
                  style={{ width: `${(order.volume / maxVol) * 70}%` }}
                />
                <div className="flex items-center gap-2 z-10 w-1/4">
                  <span className="text-[10px] text-muted-foreground/60 w-6 text-left font-medium">
                    买{order.level}
                  </span>
                </div>

                <span className="text-destructive font-bold z-10 w-1/3 text-right tabular-nums">
                  {order.price.toFixed(2)}
                </span>

                <span className="text-[10px] font-medium text-muted-foreground/80 z-10 w-1/3 text-right tabular-nums">
                  {(order.volume / 100).toFixed(0)}
                </span>
              </div>
            ));
          })()}
        </div>
      </div>
    </Card>
  );
}

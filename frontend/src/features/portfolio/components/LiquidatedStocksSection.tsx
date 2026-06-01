import { TrendingUp, TrendingDown, History, BarChart2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import type { LiquidatedStock } from '../types';

interface LiquidatedStocksSectionProps {
  liquidatedStocks: LiquidatedStock[];
}

// 获取股票图标文字
function getStockIconText(name: string): string {
  if (!name || name.length === 0) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

export function LiquidatedStocksSection({
  liquidatedStocks,
}: LiquidatedStocksSectionProps) {
  if (liquidatedStocks.length === 0) {
    return (
      <Card className="p-12 text-center bg-background/60 backdrop-blur-sm border-muted/20">
        <div className="flex flex-col items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-muted/20 flex items-center justify-center">
            <History className="w-8 h-8 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-foreground">
              暂无清仓记录
            </h3>
            <p className="text-muted-foreground mt-1">
              您还没有任何已清仓的股票
            </p>
          </div>
        </div>
      </Card>
    );
  }

  const totalRealizedPnL = liquidatedStocks.reduce(
    (sum, stock) => sum + stock.realizedPnL,
    0
  );

  return (
    <div className="space-y-4">
      {/* Summary Inline Card */}
      <div className="flex items-center justify-between p-4 bg-muted/30 backdrop-blur-md rounded-xl border border-white/10 shadow-sm">
        <div className="flex items-center gap-2">
          <BarChart2 className="w-5 h-5 text-muted-foreground" />
          <span className="text-sm font-medium text-muted-foreground">
            清仓总盈亏
          </span>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <span className="text-xs text-muted-foreground mr-2">
              共 {liquidatedStocks.length} 笔交易
            </span>
            <span
              className={cn(
                'text-xl font-bold',
                totalRealizedPnL >= 0 ? 'text-success' : 'text-destructive'
              )}
            >
              {totalRealizedPnL >= 0 ? '+' : ''}
              {formatCurrency(totalRealizedPnL)}
            </span>
          </div>
        </div>
      </div>

      {/* Glassmorphism Table Container */}
      <Card className="overflow-hidden border-0 shadow-lg bg-background/40 backdrop-blur-xl ring-1 ring-white/10">
        <Table>
          <TableHeader className="bg-muted/30">
            <TableRow className="hover:bg-transparent border-b border-white/5">
              <TableHead>股票信息</TableHead>
              <TableHead className="text-right">清仓数量</TableHead>
              <TableHead className="text-right">清仓价格</TableHead>
              <TableHead className="text-right">清仓日期</TableHead>
              <TableHead className="text-right">实现盈亏</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {liquidatedStocks.map(stock => {
              const isProfitable = stock.realizedPnL >= 0;

              return (
                <TableRow
                  key={stock.id}
                  className="transition-colors hover:bg-muted/30 border-b border-white/5"
                >
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-muted/50 rounded-lg flex items-center justify-center shrink-0 border border-white/10">
                        <span className="text-muted-foreground text-xs font-bold">
                          {getStockIconText(stock.name)}
                        </span>
                      </div>
                      <div>
                        <div className="font-medium text-sm flex items-center gap-2">
                          {stock.name}
                          <Badge
                            variant="secondary"
                            className="text-[10px] h-4 px-1"
                          >
                            已清仓
                          </Badge>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {stock.symbol}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="font-medium text-sm">
                      {stock.quantity.toLocaleString()}
                    </div>
                    <div className="text-xs text-muted-foreground">股</div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-sm">
                      {formatCurrency(stock.sellPrice)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-sm">
                      {new Date(stock.sellDate).toLocaleDateString('zh-CN')}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className={cn(
                        'font-medium flex items-center justify-end gap-1',
                        isProfitable ? 'text-success' : 'text-destructive'
                      )}
                    >
                      {isProfitable ? (
                        <TrendingUp className="h-3 w-3" />
                      ) : (
                        <TrendingDown className="h-3 w-3" />
                      )}
                      {isProfitable ? '+' : ''}
                      {formatCurrency(stock.realizedPnL)}
                    </div>
                    <div
                      className={cn(
                        'text-xs',
                        isProfitable ? 'text-success' : 'text-destructive'
                      )}
                    >
                      {formatPercent(stock.realizedPnLPercent)}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}

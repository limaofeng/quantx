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
import { financialToneClass } from '@/shared/utils/financialColors';
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

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCurrencyOrDash(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null || amount <= 0 ? '--' : formatCurrency(amount);
}

function formatSignedCurrencyOrDash(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return `${amount >= 0 ? '+' : ''}${formatCurrency(amount)}`;
}

function formatPercentOrDash(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return formatPercent(amount);
}

export function LiquidatedStocksSection({
  liquidatedStocks,
}: LiquidatedStocksSectionProps) {
  if (liquidatedStocks.length === 0) {
    return (
      <Card className="p-ui-empty text-center bg-background/60 backdrop-blur-sm border-muted/20">
        <div className="flex flex-col items-center gap-ui-section">
          <div className="w-16 h-16 rounded-full bg-muted/20 flex items-center justify-center">
            <History className="w-8 h-8 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-ui-heading font-semibold text-foreground">
              暂无真实清仓回报
            </h3>
            <p className="text-muted-foreground mt-1">
              只有真实委托或成交回报会显示在这里
            </p>
          </div>
        </div>
      </Card>
    );
  }

  const realizedPnLValues = liquidatedStocks
    .map(stock => toFiniteNumber(stock.realizedPnL))
    .filter((value): value is number => value !== null);
  const totalRealizedPnL = realizedPnLValues.reduce(
    (sum, value) => sum + value,
    0
  );
  const hasRealizedPnL = realizedPnLValues.length > 0;

  return (
    <div className="space-y-ui-section">
      {/* Summary Inline Card */}
      <div className="flex items-center justify-between p-ui-section bg-muted/30 backdrop-blur-md rounded-md border border-white/10 shadow-sm">
        <div className="flex items-center gap-2">
          <BarChart2 className="w-5 h-5 text-muted-foreground" />
          <span className="text-ui-body font-medium text-muted-foreground">
            清仓总盈亏
          </span>
        </div>
        <div className="flex items-center gap-ui-section">
          <div className="text-right">
            <span className="text-ui-label text-muted-foreground mr-2">
              共 {liquidatedStocks.length} 笔交易
            </span>
            <span
              className={cn(
                'text-ui-page-title font-bold',
                !hasRealizedPnL
                  ? 'text-slate-400'
                  : financialToneClass(totalRealizedPnL)
              )}
            >
              {hasRealizedPnL
                ? `${totalRealizedPnL >= 0 ? '+' : ''}${formatCurrency(
                    totalRealizedPnL
                  )}`
                : '--'}
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
              const realizedPnL = toFiniteNumber(stock.realizedPnL);
              const isProfitable = realizedPnL === null || realizedPnL >= 0;

              return (
                <TableRow
                  key={stock.id}
                  className="transition-colors hover:bg-muted/30 border-b border-white/5"
                >
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-muted/50 rounded-lg flex items-center justify-center shrink-0 border border-white/10">
                        <span className="text-muted-foreground text-ui-label font-bold">
                          {getStockIconText(stock.name)}
                        </span>
                      </div>
                      <div>
                        <div className="font-medium text-ui-body flex items-center gap-2">
                          {stock.name}
                          <Badge
                            variant="secondary"
                            className="text-ui-caption h-4 px-1"
                          >
                            {stock.status || '真实回报'}
                          </Badge>
                        </div>
                        <div className="text-ui-label text-muted-foreground">
                          {stock.symbol}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="font-medium text-ui-body">
                      {stock.quantity.toLocaleString()}
                    </div>
                    <div className="text-ui-label text-muted-foreground">
                      股
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-ui-body">
                      {formatCurrencyOrDash(stock.sellPrice)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-ui-body">
                      {new Date(stock.sellDate).toLocaleDateString('zh-CN')}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className={cn(
                        'font-medium flex items-center justify-end gap-1',
                        realizedPnL === null
                          ? 'text-slate-400'
                          : financialToneClass(realizedPnL)
                      )}
                    >
                      {realizedPnL === null ? null : isProfitable ? (
                        <TrendingUp className="h-3 w-3" />
                      ) : (
                        <TrendingDown className="h-3 w-3" />
                      )}
                      {formatSignedCurrencyOrDash(stock.realizedPnL)}
                    </div>
                    <div
                      className={cn(
                        'text-ui-label',
                        realizedPnL === null
                          ? 'text-slate-500'
                          : financialToneClass(realizedPnL)
                      )}
                    >
                      {formatPercentOrDash(stock.realizedPnLPercent)}
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

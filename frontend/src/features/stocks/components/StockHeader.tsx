import { ArrowLeft, DollarSign } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';

import type { StockDetail } from '../types';

interface StockHeaderProps {
  stock: StockDetail;
}

// 获取股票图标文字
function getStockIconText(name: string): string {
  if (!name || name.length === 0) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

export function StockHeader({ stock }: StockHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-6">
      <div className="flex items-center">
        <Link href="/holdings">
          <Button
            variant="ghost"
            size="sm"
            className="mr-4"
            data-testid="back-to-holdings"
          >
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
        </Link>
        <div className="flex items-center">
          <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
            <span className="text-primary text-lg font-medium">
              {getStockIconText(stock.name!)}
            </span>
          </div>
          <div>
            <h1 className="text-2xl font-bold" data-testid="stock-name">
              {stock.name} ({stock.id!})
            </h1>
            <p className="text-muted-foreground">{stock.market}</p>
          </div>
        </div>
      </div>
      <div className="flex gap-3">
        <Link href="/trading">
          <Button data-testid="trade-stock">
            <DollarSign className="mr-2 h-4 w-4" />
            交易
          </Button>
        </Link>
      </div>
    </div>
  );
}

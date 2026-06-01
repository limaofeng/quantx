import { Wallet, PieChart, DollarSign } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { formatCurrency } from '@/utils/transform/data';

interface LiquidationStatsProps {
  totalMarketValue: number;
  totalLiquidatedPnL: number;
  availableCash: number; // In a real app, this might come from a portfolio API
}

export function LiquidationStats({
  totalMarketValue,
  totalLiquidatedPnL,
  availableCash,
}: LiquidationStatsProps) {
  return (
    <div className="grid gap-4 md:grid-cols-3 mb-6">
      <Card className="p-6 bg-gradient-to-br from-blue-500/10 via-blue-500/5 to-transparent border-blue-200/20 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <div className="p-3 bg-blue-500/10 rounded-xl">
            <PieChart className="w-6 h-6 text-blue-500" />
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">
              当前持仓市值
            </p>
            <h3 className="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-blue-400">
              {formatCurrency(totalMarketValue)}
            </h3>
          </div>
        </div>
      </Card>

      <Card className="p-6 bg-gradient-to-br from-purple-500/10 via-purple-500/5 to-transparent border-purple-200/20 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <div className="p-3 bg-purple-500/10 rounded-xl">
            <Wallet className="w-6 h-6 text-purple-500" />
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">
              已清仓盈亏
            </p>
            <h3
              className={`text-2xl font-bold ${
                totalLiquidatedPnL >= 0 ? 'text-success' : 'text-destructive'
              }`}
            >
              {totalLiquidatedPnL > 0 ? '+' : ''}
              {formatCurrency(totalLiquidatedPnL)}
            </h3>
          </div>
        </div>
      </Card>

      <Card className="p-6 bg-gradient-to-br from-emerald-500/10 via-emerald-500/5 to-transparent border-emerald-200/20 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <div className="p-3 bg-emerald-500/10 rounded-xl">
            <DollarSign className="w-6 h-6 text-emerald-500" />
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">
              可用现金(模拟)
            </p>
            <h3 className="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-emerald-600 to-emerald-400">
              {formatCurrency(availableCash)}
            </h3>
          </div>
        </div>
      </Card>
    </div>
  );
}

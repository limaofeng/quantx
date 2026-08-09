import { useQuery } from '@apollo/client/react';
import {
  Wallet,
  TrendingUp,
  PieChart,
  Bot,
  Plus,
  Hand,
  ArrowRight,
} from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  getStockIconText,
  formatCurrency,
  formatPercent,
} from '@/shared/utils/format';

import { GET_DASHBOARD_SUMMARY, GET_HOLDINGS } from '@/graphql/queries';
import { marketIndices } from '@/lib/mockData';
import { DashboardSummary, EnrichedHolding } from '@/lib/types';

export default function Dashboard() {
  const userId = 'demo-user';

  const {
    data: summaryData,
    loading: summaryLoading,
    error: summaryError,
  } = useQuery(GET_DASHBOARD_SUMMARY, {
    variables: { userId },
  });

  const {
    data: holdingsData,
    loading: holdingsLoading,
    error: holdingsError,
  } = useQuery(GET_HOLDINGS, {
    variables: { userId },
  });

  const summary = summaryData?.dashboardSummary;
  const holdings = holdingsData?.holdings || [];

  const topHoldings = holdings.slice(0, 3);

  if (summaryLoading) {
    return <div>加载中...</div>;
  }

  return (
    <div>
      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">总资产</p>
              <p
                className="text-2xl font-bold text-foreground"
                data-testid="metric-total-assets"
              >
                {formatCurrency(summary?.totalAssets || 0)}
              </p>
            </div>
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center">
              <Wallet className="text-primary h-6 w-6" />
            </div>
          </div>
          <div className="flex items-center mt-4 text-sm">
            <span className="text-success" data-testid="metric-total-change">
              +2.4%
            </span>
            <span className="text-muted-foreground ml-2">较昨日</span>
          </div>
        </Card>

        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">今日盈亏</p>
              <p
                className="text-2xl font-bold text-success"
                data-testid="metric-today-pnl"
              >
                {summary?.todayReturn
                  ? `+${formatCurrency(summary.todayReturn)}`
                  : '+¥12,480'}
              </p>
            </div>
            <div className="w-12 h-12 bg-success/10 rounded-lg flex items-center justify-center">
              <TrendingUp className="text-success h-6 w-6" />
            </div>
          </div>
          <div className="flex items-center mt-4 text-sm">
            <span className="text-success" data-testid="metric-today-return">
              +1.01%
            </span>
            <span className="text-muted-foreground ml-2">收益率</span>
          </div>
        </Card>

        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">持仓股票</p>
              <p
                className="text-2xl font-bold text-foreground"
                data-testid="metric-holdings-count"
              >
                {summary?.holdingsCount || 0}
              </p>
            </div>
            <div className="w-12 h-12 bg-accent rounded-lg flex items-center justify-center">
              <PieChart className="text-accent-foreground h-6 w-6" />
            </div>
          </div>
          <div className="flex items-center mt-4 text-sm">
            <span className="text-muted-foreground">盈利</span>
            <span
              className="text-success ml-1"
              data-testid="metric-profitable-holdings"
            >
              {summary?.profitableHoldings || 0}
            </span>
            <span className="text-muted-foreground ml-2">亏损</span>
            <span
              className="text-destructive ml-1"
              data-testid="metric-losing-holdings"
            >
              {summary?.losingHoldings || 0}
            </span>
          </div>
        </Card>

        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">活跃策略</p>
              <p
                className="text-2xl font-bold text-foreground"
                data-testid="metric-strategies-count"
              >
                {summary?.strategiesCount || 0}
              </p>
            </div>
            <div className="w-12 h-12 bg-warning/10 rounded-lg flex items-center justify-center">
              <Bot className="text-warning h-6 w-6" />
            </div>
          </div>
          <div className="flex items-center mt-4 text-sm">
            <span className="text-muted-foreground">运行中</span>
            <span
              className="text-success ml-1"
              data-testid="metric-running-strategies"
            >
              {summary?.runningStrategies || 0}
            </span>
            <span className="text-muted-foreground ml-2">暂停</span>
            <span
              className="text-warning ml-1"
              data-testid="metric-paused-strategies"
            >
              {summary?.pausedStrategies || 0}
            </span>
          </div>
        </Card>
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <Card className="p-6">
          <h3 className="text-lg font-semibold mb-4">快速操作</h3>
          <div className="grid grid-cols-2 gap-4">
            <Link href="/trading">
              <Button
                className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
                data-testid="quick-action-buy"
              >
                <Plus className="mr-2 h-4 w-4" />
                买入股票
              </Button>
            </Link>
            <Link href="/liquidation">
              <Button
                variant="destructive"
                className="w-full"
                data-testid="quick-action-liquidate"
              >
                <Hand className="mr-2 h-4 w-4" />
                一键清仓
              </Button>
            </Link>
          </div>
        </Card>

        <Card className="p-6">
          <h3 className="text-lg font-semibold mb-4">市场状态</h3>
          <div className="space-y-3">
            {marketIndices.map(index => (
              <div
                key={index.name}
                className="flex justify-between items-center"
              >
                <span className="text-muted-foreground">{index.name}</span>
                <div className="text-right">
                  <span
                    className="font-medium"
                    data-testid={`market-${index.name}-value`}
                  >
                    {index.value}
                  </span>
                  <span
                    className={`text-sm ml-2 ${index.isPositive ? 'text-success' : 'text-destructive'}`}
                    data-testid={`market-${index.name}-change`}
                  >
                    {index.change}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Top Holdings */}
      <Card className="p-6">
        <h3 className="text-lg font-semibold mb-4">主要持仓</h3>
        <div className="space-y-4">
          {holdingsLoading ? (
            <div>加载持仓数据中...</div>
          ) : topHoldings.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无持仓数据
            </div>
          ) : (
            topHoldings.map(holding => {
              const stock = holding.stock;
              if (!stock) return null;

              const currentValue =
                holding.quantity * parseFloat(stock.currentPrice);
              const cost = holding.quantity * parseFloat(holding.averageCost);
              const pnl = currentValue - cost;
              const pnlPercent = ((currentValue - cost) / cost) * 100;

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
                        {holding.quantity}股 • 成本
                        {formatCurrency(holding.averageCost)}
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
            })
          )}
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
    </div>
  );
}

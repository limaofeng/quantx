// Dashboard 主页面组件
import { Wallet, TrendingUp, PieChart, Bot } from 'lucide-react';

import { formatCurrency } from '@/shared/utils/format';

import { MarketStatus } from '../components/MarketStatus';
import { MetricCard } from '../components/MetricCard';
import { QuickActions } from '../components/QuickActions';
import { TopHoldings } from '../components/TopHoldings';
import { useCurrentAccount } from '../hooks';

export default function DashboardPage() {
  const { data: accountData, loading: accountLoading } = useCurrentAccount();

  // 仪表板汇总信息暂时使用模拟数据，待后端支持相关查询
  const summary = {
    totalAsset: accountData?.currentAccount?.totalAsset || 1248560,
    todayPnL: 12480,
    todayPnLPercent: 1.25,
    totalReturn: 156000,
    totalReturnPercent: 14.2,
    activePositions: 12,
    todayTrades: 3,
    marketStatus: '交易中',
  };

  const isLoading = accountLoading;

  // 模拟的 holdings 数据和加载状态
  const topHoldings: any[] = [];
  const holdingsLoading = false;

  if (isLoading && !summary) {
    return <div>加载中...</div>;
  }

  return (
    <div className="space-y-8">
      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <MetricCard
          title="总资产"
          value={formatCurrency(summary?.totalAsset || 0)}
          change="+2.4%"
          changeLabel="较昨日"
          icon={Wallet}
          testId="metric-total-assets"
        />

        <MetricCard
          title="今日盈亏"
          value={
            summary?.todayPnL
              ? `+${formatCurrency(summary.todayPnL)}`
              : '+¥12,480'
          }
          change={`${summary?.todayPnLPercent || 0}%`}
          changeLabel="收益率"
          icon={TrendingUp}
          variant="success"
          testId="metric-today-pnl"
        />

        <MetricCard
          title="持仓股票"
          value={String(summary?.activePositions || 0)}
          icon={PieChart}
          testId="metric-holdings-count"
        />

        <MetricCard
          title="今日交易"
          value={String(summary?.todayTrades || 0)}
          icon={Bot}
          variant="warning"
          testId="metric-strategies-count"
        >
          <span className="text-slate-500 dark:text-slate-400 text-sm">
            交易次数
          </span>
          <span
            className="text-emerald-600 dark:text-emerald-400 ml-1 font-semibold"
            data-testid="metric-today-trades"
          >
            {summary?.todayTrades || 0}
          </span>
        </MetricCard>
      </div>

      {/* Quick Actions and Market Status */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <QuickActions />
        <MarketStatus />
      </div>

      {/* Top Holdings */}
      <TopHoldings holdings={topHoldings} isLoading={holdingsLoading} />
    </div>
  );
}

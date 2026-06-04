// Dashboard 主页面组件
import { Wallet, TrendingUp, PieChart, Bot } from 'lucide-react';

import type { EnrichedHolding } from '@/shared/types';
import { formatCurrency } from '@/shared/utils/format';

import { DashboardStudioShell } from '../components/DashboardStudioShell';
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
  const topHoldings: EnrichedHolding[] = [];
  const holdingsLoading = false;

  if (isLoading && !summary) {
    return <div>加载中...</div>;
  }

  return (
    <DashboardStudioShell
      content={
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-4">
            <div className="min-w-0">
              <div className="truncate text-xs font-black uppercase tracking-[0.2em] text-slate-200">
                QuantX 仪表板
              </div>
              <div className="truncate text-[10px] font-medium text-slate-600">
                账户、市场、任务入口和关键风险状态
              </div>
            </div>
            <div className="flex items-center gap-3 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              <span className="inline-flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                {summary.marketStatus}
              </span>
              <span className="text-slate-700">|</span>
              <span>Mock Snapshot</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            <div className="grid grid-cols-4 gap-3">
              <MetricCard
                title="总资产"
                value={formatCurrency(summary.totalAsset)}
                change="+2.4%"
                changeLabel="较昨日"
                icon={Wallet}
                testId="metric-total-assets"
              />

              <MetricCard
                title="今日盈亏"
                value={
                  summary.todayPnL
                    ? `+${formatCurrency(summary.todayPnL)}`
                    : '+¥12,480'
                }
                change={`${summary.todayPnLPercent}%`}
                changeLabel="收益率"
                icon={TrendingUp}
                variant="success"
                testId="metric-today-pnl"
              />

              <MetricCard
                title="持仓股票"
                value={String(summary.activePositions)}
                icon={PieChart}
                testId="metric-holdings-count"
              />

              <MetricCard
                title="今日交易"
                value={String(summary.todayTrades)}
                icon={Bot}
                variant="warning"
                testId="metric-strategies-count"
              >
                <span className="text-xs text-slate-500">交易次数</span>
                <span
                  className="ml-1 font-semibold text-emerald-300"
                  data-testid="metric-today-trades"
                >
                  {summary.todayTrades}
                </span>
              </MetricCard>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <QuickActions />
              <MarketStatus />
            </div>

            <div className="mt-3">
              <TopHoldings holdings={topHoldings} isLoading={holdingsLoading} />
            </div>
          </div>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            仪表板在线
          </span>
          <span className="text-slate-700">|</span>
          <span>市场状态: {summary.marketStatus}</span>
        </>
      }
      statusBarRight={
        <>
          <span>总资产 {formatCurrency(summary.totalAsset)}</span>
          <span className="text-slate-700">|</span>
          <span>今日交易 {summary.todayTrades}</span>
        </>
      }
    />
  );
}

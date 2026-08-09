// Dashboard 主页面组件
import { Wallet, TrendingUp, PieChart, Bot } from 'lucide-react';

import { useAccountOverview } from '@/features/account/hooks/useAccountCenter';
import { daysAgoKey, shanghaiDateKey } from '@/features/account/utils';
import { useTodayTrades } from '@/features/trading/hooks/useTrading';
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
  const account = accountData?.currentAccount;
  const overview = useAccountOverview(
    account?.id,
    daysAgoKey(7),
    shanghaiDateKey(),
    false
  );
  const trades = useTodayTrades(account?.id);
  const topHoldings: EnrichedHolding[] = overview.positions
    .slice(0, 5)
    .map(position => {
      const lastPrice = position.quote?.lastPrice ?? position.lastPrice ?? 0;
      return {
        id: position.id,
        stockCode: position.stockCode,
        stockName: position.instrumentName || position.stockCode,
        volume: position.volume,
        canUseVolume: position.canUseVolume,
        openPrice: position.avgPrice ?? 0,
        marketValue: position.marketValue ?? 0,
        frozenVolume: 0,
        onRoadVolume: 0,
        yesterdayVolume: 0,
        avgPrice: position.avgPrice ?? 0,
        lastPrice,
        profitRate: position.profitRate ?? 0,
        profitLoss: position.profitLoss ?? 0,
        stock: {
          id: position.stockCode,
          stockCode: position.stockCode,
          code: position.stockCode,
          name: position.instrumentName || position.stockCode,
          currentPrice: lastPrice,
        },
      };
    });
  const isLoading = accountLoading || overview.loading;

  if (isLoading && !account) {
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
              <div className="truncate text-[11px] font-medium text-slate-400">
                账户、市场、任务入口和关键风险状态
              </div>
            </div>
            <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-wider text-slate-400">
              <span className="inline-flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                {account ? '账户已连接' : '账户未连接'}
              </span>
              <span className="text-slate-700">|</span>
              <span>miniQMT 实时账户</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                title="总资产"
                value={
                  typeof account?.totalAsset === 'number'
                    ? formatCurrency(account.totalAsset)
                    : '--'
                }
                icon={Wallet}
                testId="metric-total-assets"
              />

              <MetricCard
                title="总盈亏"
                value={
                  typeof account?.totalProfitLoss === 'number'
                    ? formatCurrency(account.totalProfitLoss)
                    : '--'
                }
                change={
                  typeof account?.profitLossPercent === 'number'
                    ? `${account.profitLossPercent.toFixed(2)}%`
                    : undefined
                }
                changeLabel="券商账户口径"
                icon={TrendingUp}
                variant="success"
                testId="metric-today-pnl"
              />

              <MetricCard
                title="持仓股票"
                value={account ? String(overview.positions.length) : '--'}
                icon={PieChart}
                testId="metric-holdings-count"
              />

              <MetricCard
                title="今日交易"
                value={account ? String(trades.trades.length) : '--'}
                icon={Bot}
                variant="warning"
                testId="metric-strategies-count"
              >
                <span className="text-xs text-slate-400">交易次数</span>
                <span
                  className="ml-1 font-semibold text-emerald-300"
                  data-testid="metric-today-trades"
                >
                  {account ? trades.trades.length : '--'}
                </span>
              </MetricCard>
            </div>

            <div className="mt-3 grid grid-cols-1 items-start gap-3 xl:grid-cols-[minmax(360px,0.8fr)_minmax(520px,1.2fr)]">
              <QuickActions />
              <MarketStatus />
            </div>

            <div className="mt-3">
              <TopHoldings
                holdings={topHoldings}
                isLoading={overview.loading}
              />
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
          <span>账户状态: {account ? '已连接' : '未连接'}</span>
        </>
      }
      statusBarRight={
        <>
          <span>
            总资产{' '}
            {typeof account?.totalAsset === 'number'
              ? formatCurrency(account.totalAsset)
              : '--'}
          </span>
          <span className="text-slate-700">|</span>
          <span>今日交易 {account ? trades.trades.length : '--'}</span>
        </>
      }
    />
  );
}

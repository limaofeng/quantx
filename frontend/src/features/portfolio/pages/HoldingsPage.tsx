import { Download, Plus } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatCurrency } from '@/utils/transform/data';

import { HoldingsList } from '../components/HoldingsList';
import { PortfolioStudioShell } from '../components/PortfolioStudioShell';
import { PortfolioSummary } from '../components/PortfolioSummary';
import { useHoldings } from '../hooks/useHoldings';

export function HoldingsPage() {
  const {
    holdings = [],
    portfolioSummary,
    dailyAssetSnapshots,
    isLoading,
    error,
    refetch,
  } = useHoldings();

  const holdingCount = holdings.length;
  if (isLoading) {
    return (
      <PortfolioStudioShell
        activeMode="HOLDINGS"
        content={
          <div className="flex h-full items-center justify-center text-sm font-medium text-slate-500">
            加载持仓数据中...
          </div>
        }
        showSidebar={false}
        statusBarLeft="持仓数据加载中"
        statusBarRight="等待 GraphQL 响应"
      />
    );
  }

  if (error) {
    return (
      <PortfolioStudioShell
        activeMode="HOLDINGS"
        content={
          <div className="flex h-full items-center justify-center p-8">
            <div className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-5 text-center">
              <p className="mb-2 text-sm font-black text-rose-200">
                加载持仓数据失败
              </p>
              <p className="mb-4 text-xs text-slate-500">{error.message}</p>
              <Button size="sm" onClick={() => refetch()}>
                重新加载
              </Button>
            </div>
          </div>
        }
        showSidebar={false}
        statusBarLeft="持仓数据异常"
        statusBarRight={error.message}
      />
    );
  }

  return (
    <PortfolioStudioShell
      activeMode="HOLDINGS"
      content={
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-4">
            <div className="min-w-0">
              <div className="truncate text-xs font-black uppercase tracking-[0.2em] text-slate-200">
                持仓
              </div>
              <div className="truncate text-[10px] font-medium text-slate-600">
                当前持仓、仓位分布、收益状态
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/10 bg-white/[0.03] text-xs text-slate-300 hover:bg-white/[0.06]"
                data-testid="export-holdings"
              >
                <Download className="mr-2 h-3.5 w-3.5" />
                导出
              </Button>
              <Link href="/holdings">
                <Button
                  size="sm"
                  className="h-8 text-xs"
                  data-testid="buy-stocks"
                >
                  <Plus className="mr-2 h-3.5 w-3.5" />
                  买入股票
                </Button>
              </Link>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            {portfolioSummary && (
              <PortfolioSummary
                summary={portfolioSummary}
                dailyAssetSnapshots={dailyAssetSnapshots}
              />
            )}

            {holdingCount === 0 ? (
              <Card className="border-white/10 bg-white/[0.03] p-8 text-center">
                <p className="text-sm text-slate-500">暂无持仓数据</p>
              </Card>
            ) : (
              <HoldingsList holdings={holdings} />
            )}
          </div>
        </div>
      }
      showSidebar={false}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            持仓在线
          </span>
          <span className="text-slate-700">|</span>
          <span>{holdingCount} 只持仓</span>
        </>
      }
      statusBarRight={
        <>
          <span>
            总资产 {formatCurrency(portfolioSummary?.totalAsset ?? 0)}
          </span>
          <span className="text-slate-700">|</span>
          <span>
            持仓市值 {formatCurrency(portfolioSummary?.totalMarketValue ?? 0)}
          </span>
        </>
      }
    />
  );
}

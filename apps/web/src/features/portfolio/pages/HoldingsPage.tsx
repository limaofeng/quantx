import { AlertTriangle, Download, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatCurrency } from '@/utils/transform/data';

import { HoldingsList } from '../components/HoldingsList';
import { PortfolioStudioShell } from '../components/PortfolioStudioShell';
import { PortfolioSummary } from '../components/PortfolioSummary';
import { useHoldings } from '../hooks/useHoldings';

export function HoldingsPage() {
  const [historyDays, setHistoryDays] = useState<60 | 120 | 180>(180);
  const {
    holdings = [],
    portfolioSummary,
    dailyAssetSnapshots,
    historyError,
    historyLoading,
    quoteError,
    snapshotPresentation,
    snapshotAsOf,
    latestQuoteAt,
    refreshHistory,
    refreshQuotes,
    isLoading,
    error,
    refetch,
    liquidateHolding,
  } = useHoldings({ historyDays, loadHistory: true });

  const holdingCount = holdings.length;
  const snapshotCurrent = snapshotPresentation.state === 'CURRENT';
  const lastSuccessfulSync = snapshotPresentation.lastSuccessfulSyncAt
    ? new Date(snapshotPresentation.lastSuccessfulSyncAt).toLocaleString()
    : '无';
  if (isLoading) {
    return (
      <PortfolioStudioShell
        activeMode="HOLDINGS"
        content={
          <div className="flex h-full items-center justify-center text-ui-body font-medium text-slate-500">
            加载持仓数据中...
          </div>
        }
        showSidebar={false}
        statusBarLeft="持仓数据加载中"
        statusBarRight="等待 GraphQL 响应"
      />
    );
  }

  if (snapshotPresentation.state === 'NO_CACHE') {
    return (
      <PortfolioStudioShell
        activeMode="HOLDINGS"
        content={
          <div className="flex h-full items-center justify-center p-ui-section">
            <div className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-ui-section text-center">
              <p className="mb-2 text-ui-body font-black text-rose-200">
                没有可展示的账户快照
              </p>
              <p className="mb-4 text-ui-label text-slate-500">
                {snapshotPresentation.message}
              </p>
              <Button size="sm" onClick={() => refetch()}>
                重新加载
              </Button>
            </div>
          </div>
        }
        showSidebar={false}
        statusBarLeft="持仓数据异常"
        statusBarRight={error?.message || '等待首次成功同步'}
      />
    );
  }

  return (
    <PortfolioStudioShell
      activeMode="HOLDINGS"
      content={
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-ui-section">
            <div className="min-w-0">
              <div className="truncate text-ui-label font-black uppercase tracking-[0.2em] text-slate-200">
                持仓
              </div>
              <div className="truncate text-ui-caption font-medium text-slate-600">
                当前持仓、仓位分布、收益状态
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-control-compact border-white/10 bg-white/[0.03] text-ui-label text-slate-300 hover:bg-white/[0.06]"
                data-testid="export-holdings"
                disabled={!snapshotCurrent}
              >
                <Download className="mr-2 h-3.5 w-3.5" />
                导出
              </Button>
              {snapshotCurrent ? (
                <Link href="/holdings">
                  <Button
                    size="sm"
                    className="h-control-compact text-ui-label"
                    data-testid="buy-stocks"
                  >
                    <Plus className="mr-2 h-3.5 w-3.5" />
                    买入股票
                  </Button>
                </Link>
              ) : (
                <Button
                  size="sm"
                  className="h-control-compact text-ui-label"
                  data-testid="buy-stocks"
                  disabled
                >
                  <Plus className="mr-2 h-3.5 w-3.5" />
                  买入股票
                </Button>
              )}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            {!snapshotCurrent && (
              <div className="mb-3 flex items-start gap-3 rounded-lg border border-amber-400/20 bg-amber-500/10 p-3 text-ui-label text-amber-100">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <p className="font-black">实时同步不可用，当前不可交易</p>
                  <p className="mt-1 text-amber-200/80">
                    {snapshotPresentation.message}
                  </p>
                  <p className="mt-1 text-ui-caption text-slate-500">
                    最近成功同步：{lastSuccessfulSync}
                  </p>
                </div>
              </div>
            )}
            <div className="mb-2 flex items-center justify-end gap-1 text-ui-caption text-slate-600">
              <span className="mr-1">资产趋势范围</span>
              {([60, 120, 180] as const).map(days => (
                <Button
                  key={days}
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setHistoryDays(days)}
                  className={
                    historyDays === days
                      ? 'h-6 bg-white/[0.08] px-2 text-ui-caption text-slate-200'
                      : 'h-6 px-2 text-ui-caption text-slate-600'
                  }
                >
                  {days} 日
                </Button>
              ))}
            </div>
            {portfolioSummary && (
              <PortfolioSummary
                summary={portfolioSummary}
                dailyAssetSnapshots={dailyAssetSnapshots}
              />
            )}
            {(historyLoading || historyError || quoteError) && (
              <div className="mb-3 flex flex-wrap items-center gap-3 text-ui-caption text-slate-500">
                {historyLoading && <span>资产曲线正在分步加载…</span>}
                {historyError && (
                  <span className="inline-flex items-center gap-2 text-amber-300">
                    资产曲线加载失败，可单独重试：{historyError.message}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-ui-caption"
                      onClick={refreshHistory}
                    >
                      重试曲线
                    </Button>
                  </span>
                )}
                {quoteError && (
                  <span className="inline-flex items-center gap-2 text-amber-300">
                    实时行情暂不可用，当前显示券商快照：{quoteError.message}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-ui-caption"
                      onClick={refreshQuotes}
                    >
                      重试行情
                    </Button>
                  </span>
                )}
              </div>
            )}

            {snapshotPresentation.isAuthoritativeEmpty ? (
              <Card className="border-white/10 bg-white/[0.03] p-ui-section text-center">
                <p className="text-ui-body text-slate-400">
                  完整券商快照已确认当前空仓
                </p>
              </Card>
            ) : holdingCount === 0 ? (
              <Card className="border-amber-400/20 bg-amber-500/5 p-ui-section text-center">
                <p className="text-ui-body text-amber-200">
                  当前无法确认持仓是否为空
                </p>
                <p className="mt-2 text-ui-label text-slate-500">
                  等待下一次成功同步；不会把请求失败或异常空列表显示为真实空仓。
                </p>
              </Card>
            ) : (
              <HoldingsList
                holdings={holdings}
                enableRealTime={false}
                onLiquidate={liquidateHolding}
                tradingDisabled={!snapshotCurrent}
              />
            )}
          </div>
        </div>
      }
      showSidebar={false}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                snapshotCurrent ? 'bg-emerald-400' : 'bg-amber-400'
              }`}
            />
            {snapshotCurrent ? '持仓在线' : '最近有效快照'}
          </span>
          <span className="text-slate-700">|</span>
          <span>{holdingCount} 只持仓</span>
        </>
      }
      statusBarRight={
        <>
          <span>
            总资产{' '}
            {portfolioSummary
              ? formatCurrency(portfolioSummary.totalAsset)
              : '--'}
          </span>
          <span className="text-slate-700">|</span>
          <span>
            持仓市值{' '}
            {portfolioSummary
              ? formatCurrency(portfolioSummary.totalMarketValue)
              : '--'}
          </span>
          <span className="text-slate-700">|</span>
          <span>
            {latestQuoteAt
              ? `实时行情 ${new Date(latestQuoteAt).toLocaleTimeString()}`
              : snapshotAsOf
                ? `券商快照 ${new Date(snapshotAsOf).toLocaleTimeString()}`
                : '等待数据时间'}
          </span>
        </>
      }
    />
  );
}

import { Activity, Building2, RefreshCw } from 'lucide-react';
import * as React from 'react';
import { useParams } from 'wouter';

import { StudioWorkbench } from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { Button } from '@/components/ui/button';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import { useTodayOrders } from '@/features/trading/hooks';
import type { Stock } from '@/shared/types';

import { StockDetailWorkspace, type StockWorkspaceView } from '../components';
import { detailWorkspaceModes } from '../components/stockWorkspaceConfig';
import { useStockDetail } from '../hooks/useStockDetail';

type OrderLike = { status?: string | null };

const ACTIVE_ORDER_STATUSES = new Set([
  'UNREPORTED',
  'WAIT_REPORTING',
  'REPORTED',
  'PART_SUCC',
]);

function normalizeStockCode(value?: string | null) {
  return (value || '').trim().toUpperCase();
}

function toWorkspaceStock(
  instrument: NonNullable<ReturnType<typeof useStockDetail>['stock']>,
  fallbackCode: string
): Stock {
  const stockCode = normalizeStockCode(instrument.id || fallbackCode);
  return {
    id: stockCode,
    stockCode,
    name: instrument.name || stockCode,
    market: instrument.market || undefined,
    type: instrument.type || undefined,
    quote: {
      lastPrice: instrument.quote?.lastPrice ?? 0,
      changePercent: instrument.quote?.changePercent ?? 0,
      change: instrument.quote?.change ?? undefined,
      volume: instrument.quote?.volume ?? undefined,
      amount: instrument.quote?.amount ?? undefined,
      open: instrument.quote?.open ?? undefined,
      high: instrument.quote?.high ?? undefined,
      low: instrument.quote?.low ?? undefined,
      preClose: instrument.quote?.preClose ?? undefined,
    },
    currentPrice: instrument.quote?.lastPrice ?? 0,
  };
}

function PageState({
  description,
  onRetry,
  title,
}: {
  description: string;
  onRetry?: () => void;
  title: string;
}) {
  return (
    <div className="studio-workspace-surface flex h-full items-center justify-center p-ui-section">
      <div className="max-w-md border border-white/5 bg-[#0b1120]/80 p-ui-section text-center">
        <Building2 className="mx-auto h-6 w-6 text-red-300" />
        <h1 className="mt-3 text-ui-body font-black text-slate-100">{title}</h1>
        <p className="mt-2 text-ui-label leading-5 text-slate-500">
          {description}
        </p>
        {onRetry && (
          <Button
            size="sm"
            variant="outline"
            className="mt-4"
            onClick={onRetry}
          >
            <RefreshCw className="mr-2 h-3.5 w-3.5" />
            重新加载
          </Button>
        )}
      </div>
    </div>
  );
}

export default function StockDetailPage() {
  const { stockCode: routeStockCode } = useParams();
  const stockCode = normalizeStockCode(routeStockCode);
  const openStudioTab = useStudioNavigate();
  const [activeView, setActiveView] =
    React.useState<StockWorkspaceView>('OVERVIEW');
  const { stock, isLoading, error, refetch } = useStockDetail(stockCode);
  const { holdings, portfolioSummary } = useHoldings();
  const { data: accountData } = useCurrentAccount();
  const account = accountData?.currentAccount;
  const { orders } = useTodayOrders(account?.id);

  const selectedStock = React.useMemo(
    () => (stock ? toWorkspaceStock(stock, stockCode) : null),
    [stock, stockCode]
  );
  const holding = React.useMemo(
    () =>
      holdings.find(item => normalizeStockCode(item.stockCode) === stockCode) ||
      null,
    [holdings, stockCode]
  );
  const activeOrderCount = React.useMemo(
    () =>
      ((orders || []) as OrderLike[]).filter(order =>
        ACTIVE_ORDER_STATUSES.has(order.status || '')
      ).length,
    [orders]
  );

  let content: React.ReactNode;
  if (!stockCode) {
    content = (
      <PageState
        title="未指定股票代码"
        description="请从持仓、选股器或全局搜索打开一个 A 股标的。"
      />
    );
  } else if (isLoading && !stock) {
    content = (
      <PageState
        title="正在加载个股工作区"
        description={`${stockCode} 行情与基本资料读取中…`}
      />
    );
  } else if (error || !selectedStock) {
    content = (
      <PageState
        title="个股数据加载失败"
        description={error?.message || '该标的不存在或行情服务暂不可用。'}
        onRetry={() => refetch()}
      />
    );
  } else {
    content = (
      <StockDetailWorkspace
        accountId={account?.id}
        accountName={account?.accountName || undefined}
        accountSummary={account}
        activeOrderCount={activeOrderCount}
        activeView={activeView}
        context="detail"
        hasActiveOrders={activeOrderCount > 0}
        holding={holding}
        holdings={holdings}
        onStockSelect={nextStock => {
          const nextCode = normalizeStockCode(
            nextStock?.stockCode || nextStock?.id
          );
          if (nextCode && nextCode !== stockCode) {
            openStudioTab(`/stock/${nextCode}`);
          }
        }}
        onViewChange={setActiveView}
        portfolioSummary={portfolioSummary}
        selectedStock={selectedStock}
        stockCode={stockCode}
      />
    );
  }

  return (
    <StudioWorkbench
      activeMode={activeView}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={detailWorkspaceModes}
      onModeChange={mode => setActiveView(mode as StockWorkspaceView)}
      showSidebar={false}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            个股研究
          </span>
          <span className="text-slate-700">|</span>
          <span>{selectedStock?.name || stockCode || '待选标的'}</span>
        </>
      }
      statusBarRight={
        <>
          <Activity className="h-3 w-3 text-blue-400" />
          <span className="font-mono">{stockCode || '--'}</span>
          <span className="text-slate-700">|</span>
          <span>{holding ? '已持仓' : '未持仓'}</span>
        </>
      }
      theme={{ icon: Building2, name: 'blue', title: '个股详情' }}
    />
  );
}

import {
  ArrowLeft,
  Copy,
  DollarSign,
  FileText,
  LineChart,
  ReceiptText,
  Target,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'wouter';

import { useStudioNavigate } from '@/components/studio-workspace';
import { Button } from '@/components/ui/button';

import PriceInfo from '../components/PriceInfo';
import {
  StockStudioShell,
  type StockStudioMode,
} from '../components/StockStudioShell';
import { useStockDetail } from '../hooks/useStockDetail';

function copyText(text: string) {
  if (!navigator.clipboard) return;
  void navigator.clipboard.writeText(text);
}

function PlaceholderPanel({
  description,
  icon: Icon,
  title,
}: {
  description: string;
  icon: typeof LineChart;
  title: string;
}) {
  return (
    <div className="flex h-[260px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-white/[0.02]">
      <div className="text-center">
        <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-red-300">
          <Icon className="h-5 w-5" />
        </div>
        <div className="mt-3 text-sm font-black text-slate-200">{title}</div>
        <div className="mt-1 max-w-[360px] text-xs font-medium text-slate-600">
          {description}
        </div>
      </div>
    </div>
  );
}

export default function StockDetailPage() {
  const { stockCode } = useParams();
  const openStudioTab = useStudioNavigate();
  const [activeMode, setActiveMode] = useState<StockStudioMode>('QUOTE');
  const { stock, isLoading, error, refetch } = useStockDetail(stockCode || '');

  if (!stockCode) {
    return (
      <StockStudioShell
        activeMode={activeMode}
        content={
          <div className="flex h-full items-center justify-center text-sm font-medium text-slate-500">
            加载股票详情中...
          </div>
        }
        onModeChange={setActiveMode}
        stockCode=""
      />
    );
  }

  if (isLoading) {
    return (
      <StockStudioShell
        activeMode={activeMode}
        content={
          <div className="flex h-full items-center justify-center text-sm font-medium text-slate-500">
            加载股票详情中...
          </div>
        }
        onModeChange={setActiveMode}
        statusBarLeft="个股数据加载中"
        statusBarRight={stockCode}
        stockCode={stockCode}
      />
    );
  }

  if (error || !stock) {
    return (
      <StockStudioShell
        activeMode={activeMode}
        content={
          <div className="flex h-full items-center justify-center p-8">
            <div className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-5 text-center">
              <p className="mb-2 text-sm font-black text-rose-200">
                加载股票详情失败
              </p>
              <p className="mb-4 text-xs text-slate-500">
                {error?.message || '股票不存在'}
              </p>
              <Button size="sm" onClick={() => refetch()}>
                重新加载
              </Button>
            </div>
          </div>
        }
        onModeChange={setActiveMode}
        statusBarLeft="个股数据异常"
        statusBarRight={error?.message || '股票不存在'}
        stockCode={stockCode}
      />
    );
  }

  const stockName = stock.name || stock.id || stockCode;
  const renderActiveContent = () => {
    if (activeMode === 'QUOTE') {
      return (
        <div className="space-y-3">
          <PriceInfo stock={stock} />
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg border border-white/10 bg-[#0f172a]/70 p-4">
              <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
                市场
              </div>
              <div className="mt-2 text-sm font-bold text-slate-200">
                {stock.market || '未知市场'}
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-[#0f172a]/70 p-4">
              <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
                标的代码
              </div>
              <div className="mt-2 font-mono text-sm font-bold text-red-100">
                {stock.id || stockCode}
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-[#0f172a]/70 p-4">
              <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
                数据状态
              </div>
              <div className="mt-2 inline-flex items-center gap-2 text-sm font-bold text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                已加载
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (activeMode === 'CHART') {
      return (
        <PlaceholderPanel
          description="后续可接入 lightweight-charts，把 K 线、成交量、指标和交易标记放在此工作区。"
          icon={LineChart}
          title="图表工作区待接入"
        />
      );
    }

    if (activeMode === 'FINANCIAL') {
      return (
        <PlaceholderPanel
          description="财务指标、估值、利润表和资产负债表快照可作为资源 tab 继续扩展。"
          icon={FileText}
          title="财务视图待接入"
        />
      );
    }

    if (activeMode === 'FLOWS') {
      return (
        <PlaceholderPanel
          description="个股相关委托、成交、持仓变动与资金流水可以在这里聚合。"
          icon={ReceiptText}
          title="交易流水待接入"
        />
      );
    }

    return (
      <PlaceholderPanel
        description="关联策略实例、回测版本、DecisionTrace 与 TradeIntent 可在这里打开。"
        icon={FileText}
        title="策略关联待接入"
      />
    );
  };

  return (
    <StockStudioShell
      activeMode={activeMode}
      content={
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-4">
            <div className="flex min-w-0 items-center gap-3">
              <Link href="/holdings">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2 text-xs text-slate-400 hover:text-slate-100"
                  data-testid="back-to-holdings"
                >
                  <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
                  返回
                </Button>
              </Link>
              <div className="min-w-0">
                <div
                  className="truncate text-xs font-black uppercase tracking-[0.2em] text-slate-200"
                  data-testid="stock-name"
                >
                  {stockName}
                </div>
                <div className="truncate font-mono text-[10px] font-medium text-slate-600">
                  {stock.market || 'MARKET'} / {stock.id || stockCode}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                className="h-8 border-white/10 bg-white/[0.03] text-xs text-slate-300 hover:bg-white/[0.06]"
                onClick={() =>
                  openStudioTab(
                    `/liquidation?symbol=${encodeURIComponent(stock.id || stockCode)}`
                  )
                }
                size="sm"
                variant="outline"
              >
                <Target className="mr-2 h-3.5 w-3.5" />
                止盈/止损计划
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/10 bg-white/[0.03] text-xs text-slate-300 hover:bg-white/[0.06]"
                onClick={() => copyText(stock.id || stockCode)}
              >
                <Copy className="mr-2 h-3.5 w-3.5" />
                复制代码
              </Button>
              <Button
                size="sm"
                className="h-8 text-xs"
                data-testid="trade-stock"
                onClick={() =>
                  openStudioTab(`/holdings?symbol=${stock.id || stockCode}`)
                }
              >
                <DollarSign className="mr-2 h-3.5 w-3.5" />
                交易
              </Button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            {renderActiveContent()}
          </div>
        </div>
      }
      onModeChange={setActiveMode}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            个股数据
          </span>
          <span className="text-slate-700">|</span>
          <span>{stockName}</span>
        </>
      }
      statusBarRight={
        <>
          <span>{stock.market || '未知市场'}</span>
          <span className="text-slate-700">|</span>
          <span className="font-mono">{stock.id || stockCode}</span>
        </>
      }
      stockCode={stock.id || stockCode}
      stockName={stockName}
    />
  );
}

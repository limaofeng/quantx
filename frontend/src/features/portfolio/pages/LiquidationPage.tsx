import {
  AlertTriangle,
  Download,
  RefreshCw,
  Briefcase,
  History,
} from 'lucide-react';
import { useState } from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CurrentHoldingsSection } from '@/features/portfolio/components/CurrentHoldingsSection';
import { LiquidatedStocksSection } from '@/features/portfolio/components/LiquidatedStocksSection';
import { LiquidationStats } from '@/features/portfolio/components/LiquidationStats';

import { useLiquidationActions } from '../hooks/useLiquidationActions';
import { useLiquidationData } from '../hooks/useLiquidationData';
import type { Position } from '../types';

export function LiquidationPage() {
  const {
    liquidatedStocks,
    currentHoldings,
    isLoading: dataLoading,
    error: dataError,
    refetch,
  } = useLiquidationData();

  const { liquidateMultiple } = useLiquidationActions();

  const [selectedHoldings, setSelectedHoldings] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState('current');

  const handleRefresh = () => {
    refetch();
  };

  const handleLiquidateSelected = async () => {
    if (selectedHoldings.length === 0) return;

    try {
      await liquidateMultiple(selectedHoldings);
      setSelectedHoldings([]);
      handleRefresh();
    } catch {
      // console.error('Liquidation failed:', err);
    }
  };

  const handleLiquidateMultiple = async (holdingIds: string[]) => {
    try {
      await liquidateMultiple(holdingIds);
      handleRefresh();
    } catch {
      // console.error('Liquidation failed:', err);
    }
  };

  if (dataLoading)
    return (
      <div className="p-8 text-center text-muted-foreground">加载数据中...</div>
    );

  if (dataError)
    return (
      <div className="p-8 text-center text-destructive">
        加载失败: {dataError.message}
      </div>
    );

  const portfolio = {
    positions: currentHoldings,
    liquidatedStocks: liquidatedStocks,
    summary: {
      totalMarketValue: currentHoldings.reduce(
        (acc, pos) => acc + (pos.marketValue || 0),
        0
      ),
      totalLiquidatedPnL: liquidatedStocks.reduce(
        (acc, stock) => acc + (stock.realizedPnL || 0),
        0
      ),
    },
  };

  return (
    <div className="flex flex-col flex-1 h-full bg-background/50">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b bg-background/60 backdrop-blur-xl supports-[backdrop-filter]:bg-background/40">
        <div>
          <h1 className="text-xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-primary to-primary/60">
            清仓管理
          </h1>
          <p className="text-xs text-muted-foreground mt-1">
            管理当前持仓和查看已清仓股票的历史记录
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-2 bg-background/50 hover:bg-accent/50 transition-all border-dashed"
            onClick={handleRefresh}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span className="text-xs">刷新数据</span>
          </Button>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                size="sm"
                variant="destructive"
                className="h-8 gap-2 shadow-sm transition-all hover:shadow-md"
              >
                <AlertTriangle className="h-3.5 w-3.5" />
                <span className="text-xs">一键清仓</span>
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认一键清仓</AlertDialogTitle>
                <AlertDialogDescription>
                  您确定要卖出所有当前持仓股票吗？
                  <br />
                  <span className="font-bold text-destructive">
                    此操作将清空您的所有持仓，且不可撤销。
                  </span>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() =>
                    handleLiquidateMultiple(
                      portfolio.positions.map((p: Position) => p.id)
                    )
                  }
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  确认全部清仓
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                size="sm"
                className="h-8 gap-2 shadow-sm bg-gradient-to-r from-emerald-600 to-emerald-500 hover:from-emerald-700 hover:to-emerald-600 border-0 text-white"
              >
                <Download className="h-3.5 w-3.5" />
                <span className="text-xs">资金赎回</span>
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>资金赎回</AlertDialogTitle>
                <AlertDialogDescription>
                  请输入您要赎回的金额。当前可用资金:{' '}
                  <span className="font-mono text-emerald-500 font-bold">
                    ¥0.00
                  </span>{' '}
                  (模拟)
                  <p className="text-xs text-muted-foreground mt-2">
                    注：这是模拟功能，实际资金赎回请在券商APP操作。
                  </p>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <div className="py-4">
                <input
                  type="number"
                  placeholder="输入金额"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction>确认赎回</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </header>

      {/* Main Content */}
      <div className="p-6 space-y-6 overflow-auto">
        {/* Stats */}
        <LiquidationStats
          totalMarketValue={portfolio.summary.totalMarketValue}
          totalLiquidatedPnL={portfolio.summary.totalLiquidatedPnL}
          availableCash={0}
        />

        {/* Tabs Console - Modern Segmented Style */}
        <Tabs
          value={activeTab}
          onValueChange={setActiveTab}
          className="space-y-6"
        >
          <div className="flex items-center">
            <TabsList className="bg-slate-100/80 dark:bg-slate-950/80 p-1 rounded-2xl border border-slate-200/50 dark:border-slate-800/50 shadow-inner flex gap-1 h-11 w-full max-w-[400px]">
              <TabsTrigger
                value="current"
                className="flex-1 rounded-xl text-[10px] font-black uppercase tracking-[0.1em] transition-all duration-300 data-[state=active]:bg-white dark:data-[state=active]:bg-slate-900 data-[state=active]:shadow-sm data-[state=active]:text-primary relative group overflow-hidden h-full"
              >
                <div className="flex items-center justify-center gap-2.5 relative z-10">
                  <Briefcase className="w-3.5 h-3.5 opacity-60 group-data-[state=active]:opacity-100 transition-opacity" />
                  <span>当前持仓</span>
                  <div className="flex items-center justify-center min-w-[20px] h-5 text-[9px] font-bold bg-slate-200/50 dark:bg-slate-800/50 group-data-[state=active]:bg-primary/10 group-data-[state=active]:text-primary rounded-lg px-1 transition-colors">
                    {portfolio.positions.length}
                  </div>
                </div>
                {/* Bottom Accent Line */}
                <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-0 h-0.5 bg-primary group-data-[state=active]:w-1/2 transition-all duration-300 rounded-full" />
              </TabsTrigger>
              <TabsTrigger
                value="liquidated"
                className="flex-1 rounded-xl text-[10px] font-black uppercase tracking-[0.1em] transition-all duration-300 data-[state=active]:bg-white dark:data-[state=active]:bg-slate-900 data-[state=active]:shadow-sm data-[state=active]:text-primary relative group overflow-hidden h-full"
              >
                <div className="flex items-center justify-center gap-2.5 relative z-10">
                  <History className="w-3.5 h-3.5 opacity-60 group-data-[state=active]:opacity-100 transition-opacity" />
                  <span>已清仓</span>
                  <div className="flex items-center justify-center min-w-[20px] h-5 text-[9px] font-bold bg-slate-200/50 dark:bg-slate-800/50 group-data-[state=active]:bg-primary/10 group-data-[state=active]:text-primary rounded-lg px-1 transition-colors">
                    {portfolio.liquidatedStocks.length}
                  </div>
                </div>
                {/* Bottom Accent Line */}
                <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-0 h-0.5 bg-primary group-data-[state=active]:w-1/2 transition-all duration-300 rounded-full" />
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent
            value="current"
            className="mt-0 focus-visible:outline-none"
          >
            <CurrentHoldingsSection
              holdings={portfolio.positions}
              selectedHoldings={selectedHoldings}
              onSelectionChange={setSelectedHoldings}
              onLiquidateSelected={handleLiquidateSelected}
              liquidateMultiple={handleLiquidateMultiple}
            />
          </TabsContent>

          <TabsContent
            value="liquidated"
            className="mt-0 focus-visible:outline-none"
          >
            <LiquidatedStocksSection
              liquidatedStocks={portfolio.liquidatedStocks}
            />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

import { format, addDays } from 'date-fns';
import {
  ArrowLeft,
  History,
  TrendingUp,
  CreditCard,
  Zap,
  Timer,
  Maximize2,
  RefreshCw,
  Wallet,
  Play,
} from 'lucide-react';
import React, { useState, useMemo } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { gql } from '@/generated/gql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';
import { TaskHistory } from '../components/TaskHistory';

const GET_REVERSE_REPO_INSTRUMENTS = gql(`
  query GetReverseRepoInstruments {
    instruments(where: { type: TRR }) {
      id
      instrumentId
      name
      market
      interestAccrualDays
      quote {
        lastPrice
        change
        changePercent
      }
    }
  }
`);

// Mock Data for Order Records - Keeping existing logic
const MOCK_ORDERS = [
  {
    id: '1',
    code: '204001',
    name: 'GC001',
    amount: '500,000',
    rate: '1.85%',
    time: '今天 15:02:11',
    status: '已成交',
  },
  {
    id: '2',
    code: '131810',
    name: 'R-001',
    amount: '1,200,000',
    rate: '1.81%',
    time: '昨天 15:10:05',
    status: '已成交',
  },
  {
    id: '3',
    code: '204001',
    name: 'GC001',
    amount: '100,000',
    rate: '1.95%',
    time: '2026/01/17',
    status: '已成交',
  },
];

export function ReverseRepoDataPage() {
  const [, setLocation] = useLocation();
  const [showTradeHistory, setShowTradeHistory] = useState(false);
  const [activeMarket, setActiveMarket] = useState('SH');
  const [orderPage] = useState(1);
  const ITEMS_PER_PAGE = 7; // Increased density

  // Simulate more mock orders for pagination testing
  const allOrders = useMemo(() => {
    const base = [...MOCK_ORDERS];
    return [...base, ...base, ...base].map((o, i) => ({ ...o, id: `${i}` }));
  }, []);

  const paginatedOrders = allOrders.slice(
    (orderPage - 1) * ITEMS_PER_PAGE,
    orderPage * ITEMS_PER_PAGE
  );

  const {
    deployment: tradeDeployment,
    isSyncing: isTrading,
    triggerSync: triggerTrade,
  } = useDeploymentSync('bond-repo-auto-trade', {
    successMessage: '逆回购交易任务已启动',
  });

  const [{ data }] = useQuery({
    query: GET_REVERSE_REPO_INSTRUMENTS as any,
  });

  const parsedData = useMemo(() => {
    if (!data?.instruments) return [];
    return data.instruments
      .map((item: any) => {
        const durationMatch = item.name.match(/\d+/);
        const duration = durationMatch ? parseInt(durationMatch[0], 10) : 0;
        const rate = item.quote?.lastPrice ?? 0;
        const days = item.interestAccrualDays ?? duration;
        const earningsPer10k = (10000 * rate * days) / 36500;
        const arrivalDate = addDays(new Date(), days);

        return {
          code: item.instrumentId,
          name: item.name,
          market: item.market === 'SH' ? '沪市' : '深市',
          rate: rate.toFixed(3),
          duration: duration.toString(),
          type: item.market,
          rawChange: item.quote?.changePercent || 0,
          earningsPer10k: earningsPer10k.toFixed(3),
          days: `${days}天`,
          arrivalDate: format(arrivalDate, 'MM-dd'),
          term: `${duration}D`,
        };
      })
      .sort((a: any, b: any) => parseInt(a.duration) - parseInt(b.duration));
  }, [data]);

  const filteredData = useMemo(
    () => parsedData.filter((item: any) => item.type === activeMarket),
    [activeMarket, parsedData]
  );

  // Calculations (Best Rates, Earnings)
  const bestRates = useMemo(() => {
    const getBest = (days: number, mkt: string) => {
      const items = parsedData.filter(
        (i: any) => i.duration === days.toString() && i.type === mkt
      );
      if (!items.length) return { rate: '0.000', rawChange: 0 };
      return items.reduce(
        (p: any, c: any) => (parseFloat(c.rate) > parseFloat(p.rate) ? c : p),
        items[0]
      );
    };
    return {
      sh1d: getBest(1, 'SH'),
      sz1d: getBest(1, 'SZ'),
      sh7d: getBest(7, 'SH'),
      sz7d: getBest(7, 'SZ'),
    };
  }, [parsedData]);

  // Mock calculation logic for earnings (simplified)
  const earnings = { total: 12580.5, month: 320.15 };

  return (
    <DataStudioPageFrame
      activeMode="REVERSE_REPO"
      description="国债逆回购、利率与交易记录"
      title="逆回购数据"
    >
      <div className="flex flex-col gap-4 animate-fade-in pb-6">
        {/* 1. Header & Controls - Compact */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg bg-white/50 dark:bg-slate-800/50 hover:bg-white dark:hover:bg-slate-800 border border-slate-200/60 dark:border-slate-700/60 shadow-sm backdrop-blur-sm transition-all"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="w-4 h-4 text-slate-600 dark:text-slate-400" />
            </Button>
            <div>
              <h1 className="text-lg font-black text-slate-800 dark:text-slate-100 tracking-tight leading-none">
                国债逆回购
              </h1>
              <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5">
                Cash Management
              </p>
            </div>
          </div>

          <DeploymentSyncControl
            deploymentName="bond-repo-sync"
            defaultFlowName="数据同步"
            historyFallbackName="逆回购数据同步"
            successMessage="国债逆回购数据同步任务已提交"
          />
        </div>

        {/* 2. Overview Cards - High Density Grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          {/* Card 1: 1-Day Best */}
          <Card className="relative overflow-hidden border-indigo-100 dark:border-indigo-500/10 shadow-sm bg-gradient-to-br from-indigo-50/50 via-white to-white dark:from-indigo-950/20 dark:via-slate-900/40 dark:to-slate-900/40">
            <div className="absolute top-0 right-0 p-2 opacity-5">
              <Zap className="w-16 h-16 text-indigo-600 dark:text-indigo-400 rotate-12" />
            </div>
            <CardContent className="p-3">
              <div className="flex items-center gap-2 mb-2">
                <div className="p-1 rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                  <Zap className="w-3 h-3" />
                </div>
                <span className="text-[10px] font-bold text-indigo-900/60 dark:text-indigo-200/60 uppercase tracking-wider">
                  最优 1天期 (1D)
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="text-[10px] text-slate-400 mb-0.5 font-medium">
                    沪市 GC001
                  </div>
                  <div className="text-sm font-black text-indigo-600 dark:text-indigo-400 tabular-nums tracking-tight">
                    {bestRates.sh1d.rate}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400 mb-0.5 font-medium">
                    深市 R-001
                  </div>
                  <div className="text-sm font-black text-indigo-600 dark:text-indigo-400 tabular-nums tracking-tight">
                    {bestRates.sz1d.rate}%
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card 2: 7-Day Best */}
          <Card className="relative overflow-hidden border-blue-100 dark:border-blue-500/10 shadow-sm bg-gradient-to-br from-blue-50/50 via-white to-white dark:from-blue-950/20 dark:via-slate-900/40 dark:to-slate-900/40">
            <div className="absolute top-0 right-0 p-2 opacity-5">
              <Timer className="w-16 h-16 text-blue-600 dark:text-blue-400 rotate-12" />
            </div>
            <CardContent className="p-3">
              <div className="flex items-center gap-2 mb-2">
                <div className="p-1 rounded-md bg-blue-500/10 text-blue-600 dark:text-blue-400">
                  <Timer className="w-3 h-3" />
                </div>
                <span className="text-[10px] font-bold text-blue-900/60 dark:text-blue-200/60 uppercase tracking-wider">
                  最优 7天期 (7D)
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="text-[10px] text-slate-400 mb-0.5 font-medium">
                    沪市 GC007
                  </div>
                  <div className="text-sm font-black text-blue-600 dark:text-blue-400 tabular-nums tracking-tight">
                    {bestRates.sh7d.rate}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400 mb-0.5 font-medium">
                    深市 R-007
                  </div>
                  <div className="text-sm font-black text-blue-600 dark:text-blue-400 tabular-nums tracking-tight">
                    {bestRates.sz7d.rate}%
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card 3: Earnings */}
          <Card className="col-span-1 md:col-span-2 relative overflow-hidden border-emerald-100 dark:border-emerald-500/10 shadow-sm bg-gradient-to-br from-emerald-50/50 via-white to-white dark:from-emerald-950/20 dark:via-slate-900/40 dark:to-slate-900/40">
            <div className="absolute -right-4 -bottom-4 opacity-[0.03]">
              <Wallet className="w-32 h-32 text-emerald-600 dark:text-emerald-400 rotate-12" />
            </div>
            <CardContent className="p-3 h-full flex items-center justify-between">
              <div className="flex flex-col justify-between h-full">
                <div className="flex items-center gap-2">
                  <div className="p-1 rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <CreditCard className="w-3 h-3" />
                  </div>
                  <span className="text-[10px] font-bold text-emerald-900/60 dark:text-emerald-200/60 uppercase tracking-wider">
                    累计收益
                  </span>
                </div>
                <div className="mt-2">
                  <div className="text-2xl font-black text-emerald-600 dark:text-emerald-400 tabular-nums tracking-tight">
                    ¥
                    {earnings.total.toLocaleString('en-US', {
                      minimumFractionDigits: 2,
                    })}
                  </div>
                  <div className="text-[10px] font-medium text-emerald-600/60 dark:text-emerald-400/60 mt-0.5">
                    本月 +¥{earnings.month.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Mini Action Area */}
              <div className="flex flex-col gap-2">
                <Button
                  size="sm"
                  className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700 shadow-sm shadow-emerald-200 dark:shadow-emerald-900/20 px-3"
                >
                  资金详情
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 3. Main Split View */}
        <div className="grid grid-cols-12 gap-4 flex-1 min-h-0">
          {/* Left: Market Data Table (8 cols) */}
          <Card className="col-span-12 lg:col-span-8 flex flex-col border-slate-200 dark:border-slate-800 shadow-sm bg-white/80 dark:bg-slate-900/60 backdrop-blur-md">
            <Tabs
              defaultValue="SH"
              onValueChange={setActiveMarket}
              className="flex-1 flex flex-col min-h-0"
            >
              <div className="px-4 py-3 flex items-center justify-between border-b border-slate-100 dark:border-slate-800/60">
                <div className="flex items-center gap-4">
                  <h2 className="text-sm font-bold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-indigo-500" />
                    行情列表
                  </h2>
                  <TabsList className="h-7 bg-slate-100 dark:bg-slate-800 p-0.5">
                    <TabsTrigger
                      value="SH"
                      className="h-6 text-[10px] px-3 data-[state=active]:bg-white dark:data-[state=active]:bg-slate-700 shadow-none"
                    >
                      沪市
                    </TabsTrigger>
                    <TabsTrigger
                      value="SZ"
                      className="h-6 text-[10px] px-3 data-[state=active]:bg-white dark:data-[state=active]:bg-slate-700 shadow-none"
                    >
                      深市
                    </TabsTrigger>
                  </TabsList>
                </div>
                <Badge
                  variant="outline"
                  className="text-[10px] font-mono h-5 px-1.5 text-slate-400 border-slate-200 dark:border-slate-800"
                >
                  {filteredData.length} Items
                </Badge>
              </div>

              <div className="flex-1 overflow-hidden">
                {/* Custom Table Header */}
                <div className="grid grid-cols-12 gap-2 px-4 py-2 bg-slate-50/50 dark:bg-slate-900/20 border-b border-slate-100 dark:border-slate-800 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                  <div className="col-span-2">期限/代码</div>
                  <div className="col-span-3">名称</div>
                  <div className="col-span-2 text-right">年化利率</div>
                  <div className="col-span-2 text-right">万元收益</div>
                  <div className="col-span-3 text-right">计息/到账</div>
                </div>

                <ScrollArea className="h-[400px] lg:h-[500px]">
                  <div className="divide-y divide-slate-100 dark:divide-slate-800/60">
                    {filteredData.map((item: any) => (
                      <div
                        key={item.code}
                        className="grid grid-cols-12 gap-2 px-4 py-2.5 items-center hover:bg-indigo-50/30 dark:hover:bg-indigo-900/10 transition-colors group cursor-default"
                      >
                        <div className="col-span-2 flex flex-col">
                          <span className="text-xs font-bold text-slate-700 dark:text-slate-200 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors">
                            {item.term}
                          </span>
                          <span className="text-[10px] font-mono text-slate-400">
                            {item.code}
                          </span>
                        </div>
                        <div className="col-span-3 text-xs font-medium text-slate-600 dark:text-slate-400">
                          {item.name}
                        </div>
                        <div className="col-span-2 text-right font-mono text-sm font-bold text-rose-500 tabular-nums bg-rose-50/0 group-hover:bg-rose-50/50 dark:group-hover:bg-rose-900/10 rounded px-1 -mr-1 transition-colors">
                          {item.rate}%
                        </div>
                        <div className="col-span-2 text-right font-mono text-xs font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
                          {item.earningsPer10k}
                        </div>
                        <div className="col-span-3 text-right flex flex-col items-end">
                          <div className="flex items-center gap-1.5">
                            <Badge
                              variant="secondary"
                              className="h-4 px-1 text-[9px] min-w-0 bg-slate-100 dark:bg-slate-800 text-slate-500"
                            >
                              {item.days}
                            </Badge>
                            <span className="text-[10px] text-slate-400 font-medium">
                              {item.arrivalDate}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            </Tabs>
          </Card>

          {/* Right: Trading & History (4 cols) */}
          <div className="col-span-12 lg:col-span-4 flex flex-col gap-4">
            {/* Trading Control */}
            <Card className="border-indigo-100 dark:border-indigo-500/10 shadow-sm bg-gradient-to-b from-white to-slate-50/50 dark:from-slate-900 dark:to-slate-900/50">
              <CardHeader className="px-4 py-3 border-b border-indigo-50 dark:border-indigo-500/10">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs font-bold text-indigo-900 dark:text-indigo-100 flex items-center gap-2">
                    <Play className="w-3.5 h-3.5" />
                    智能交易
                  </CardTitle>
                  <div className="flex items-center gap-1.5">
                    <div
                      className={cn(
                        'w-2 h-2 rounded-full',
                        tradeDeployment?.status === 'Running'
                          ? 'bg-emerald-500 animate-pulse'
                          : 'bg-slate-300'
                      )}
                    />
                    <span className="text-[10px] font-medium text-slate-500">
                      {tradeDeployment?.status || 'Idle'}
                    </span>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="p-4 space-y-4">
                <div className="grid grid-cols-2 gap-2">
                  <div className="p-2 rounded-lg bg-white dark:bg-slate-950 border border-slate-200 dark:border-slate-800">
                    <div className="text-[9px] text-slate-400 uppercase tracking-wider mb-1">
                      Max Vol
                    </div>
                    <div className="text-sm font-black text-slate-700 dark:text-slate-200">
                      1000{' '}
                      <span className="text-[10px] font-normal text-slate-400">
                        张
                      </span>
                    </div>
                  </div>
                  <div className="p-2 rounded-lg bg-white dark:bg-slate-950 border border-slate-200 dark:border-slate-800">
                    <div className="text-[9px] text-slate-400 uppercase tracking-wider mb-1">
                      Next Run
                    </div>
                    <div className="text-sm font-bold text-slate-700 dark:text-slate-200 truncate">
                      {tradeDeployment?.nextRunTime
                        ? format(new Date(tradeDeployment.nextRunTime), 'HH:mm')
                        : '--:--'}
                    </div>
                  </div>
                </div>

                <Button
                  className={cn(
                    'w-full h-8 text-xs font-bold shadow-md transition-all active:scale-95',
                    isTrading
                      ? 'bg-slate-100 text-slate-400 shadow-none cursor-not-allowed'
                      : 'bg-indigo-600 hover:bg-indigo-700 text-white shadow-indigo-500/20'
                  )}
                  onClick={() => {
                    void triggerTrade();
                  }}
                  disabled={isTrading}
                >
                  {isTrading ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin mr-2" />
                  ) : (
                    <Zap className="w-3.5 h-3.5 fill-current mr-2" />
                  )}
                  {isTrading ? 'Execution in progress...' : 'Execute Strategy'}
                </Button>
              </CardContent>
            </Card>

            {/* Compact Order History */}
            <Card className="flex-1 min-h-[300px] flex flex-col border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/20 flex items-center justify-between">
                <span className="text-xs font-bold text-slate-600 dark:text-slate-300 flex items-center gap-2">
                  <History className="w-3.5 h-3.5" />
                  Recent Orders
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5"
                  onClick={() => setShowTradeHistory(true)}
                >
                  <Maximize2 className="w-3 h-3 text-slate-400" />
                </Button>
              </div>
              <ScrollArea className="flex-1 bg-white dark:bg-slate-950/20">
                <div className="divide-y divide-slate-50 dark:divide-slate-900">
                  {paginatedOrders.map(order => (
                    <div
                      key={order.id}
                      className="p-3 hover:bg-slate-50 dark:hover:bg-slate-900/40 transition-colors"
                    >
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[10px] font-bold text-slate-700 dark:text-slate-300">
                          {order.name}
                        </span>
                        <span className="text-[9px] font-mono text-slate-400">
                          {order.time.split(' ')[1]}
                        </span>
                      </div>
                      <div className="flex justify-between items-center">
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="secondary"
                            className="h-4 px-1 text-[9px] bg-slate-100 dark:bg-slate-800 text-slate-500 font-mono"
                          >
                            {order.code}
                          </Badge>
                          <span className="text-[10px] font-mono font-medium text-rose-500">
                            {order.rate}
                          </span>
                        </div>
                        <span className="text-[10px] font-medium text-emerald-600">
                          {order.status}
                        </span>
                      </div>
                    </div>
                  ))}
                  {!paginatedOrders.length && (
                    <div className="p-8 text-center text-[10px] text-slate-300">
                      No orders found
                    </div>
                  )}
                </div>
              </ScrollArea>
            </Card>
          </div>
        </div>

        <TaskHistory
          open={showTradeHistory}
          onOpenChange={setShowTradeHistory}
          deploymentId={tradeDeployment?.id}
          deploymentName={tradeDeployment?.flowName || '逆回购交易任务'}
          workPoolName={tradeDeployment?.workPoolName}
        />
      </div>
    </DataStudioPageFrame>
  );
}

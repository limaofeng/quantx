import { ArrowLeft, LayoutGrid, List as ListIcon } from 'lucide-react';
import React, { useMemo } from 'react';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';
import { HoldingsDataList } from '../components/HoldingsDataList';
import { HoldingsStatsCards } from '../components/HoldingsStatsCards';
import { SectorHoldingCard } from '../components/SectorHoldingCard';

// Mock Data for Holdings with Sector Info
const MOCK_HOLDINGS_DATA = Array(26)
  .fill(null)
  .map((_, i) => ({
    code: i % 2 === 0 ? '600519.SH' : '300750.SZ',
    name: i % 2 === 0 ? '贵州茅台' : '宁德时代',
    sector: i % 3 === 0 ? '食品饮料' : i % 3 === 1 ? '电力设备' : '银行', // Mock Sector
    cacheStatus: {
      tickRange: '2024-01-01 -> 2024-03-20',
      k1mRange: '2024-01-01 -> 2024-03-20',
      kDayRange: '2020-01-01 -> 2024-03-20',
    },
    lastSync: '10分钟前',
  }));

export function HoldingsDataSyncPage() {
  const [, setLocation] = useLocation();

  const holdingsCount = MOCK_HOLDINGS_DATA.length;

  // Group by Sector
  const groupedHoldings = useMemo(() => {
    const groups: Record<string, typeof MOCK_HOLDINGS_DATA> = {};
    MOCK_HOLDINGS_DATA.forEach(item => {
      const sector = item.sector || 'Uncategorized';
      if (!groups[sector]) {
        groups[sector] = [];
      }
      groups[sector].push(item);
    });
    return groups;
  }, []);

  const sectorCount = Object.keys(groupedHoldings).length;

  return (
    <DataStudioPageFrame
      activeMode="HOLDINGS"
      description="本地持仓数据同步"
      title="持仓数据同步"
    >
      <div className="flex h-[calc(100vh-var(--header-height)-4rem)] flex-col gap-4 animate-fade-in">
        {/* Compact Header Section */}
        <div className="flex items-center justify-between gap-4 py-1">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg bg-white/50 dark:bg-white/5 border border-slate-200/60 dark:border-white/5 shadow-sm hover:scale-105 active:scale-95 transition-all backdrop-blur-sm"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="w-4 h-4 text-slate-600 dark:text-slate-400" />
            </Button>
            <div>
              <h1 className="text-lg font-black text-slate-900 dark:text-white tracking-tight leading-none">
                持仓数据管理
              </h1>
              <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-80">
                {holdingsCount} 个活跃持仓 • {sectorCount} 个所属板块
              </p>
            </div>
          </div>

          <DeploymentSyncControl
            deploymentName="position-sync"
            defaultFlowName="持仓数据同步"
            successMessage="持仓数据同步任务已提交"
          />
        </div>

        {/* Dashboard Stats */}
        <div className="shrink-0">
          <HoldingsStatsCards
            totalCount={holdingsCount}
            syncHealth={98} // Mock
            sectorCount={sectorCount}
          />
        </div>

        {/* Main Content Area with Tabs */}
        <div className="flex-1 min-h-0 flex flex-col gap-2">
          <Tabs defaultValue="grid" className="w-full h-full flex flex-col">
            <div className="flex items-center justify-between px-1">
              <TabsList className="bg-slate-100/50 dark:bg-white/5 p-1 rounded-lg h-8">
                <TabsTrigger
                  value="grid"
                  className="gap-2 h-6 text-[10px] data-[state=active]:bg-white dark:data-[state=active]:bg-slate-800"
                >
                  <LayoutGrid className="w-3.5 h-3.5" />
                  <span className="font-bold">板块视图</span>
                </TabsTrigger>
                <TabsTrigger
                  value="list"
                  className="gap-2 h-6 text-[10px] data-[state=active]:bg-white dark:data-[state=active]:bg-slate-800"
                >
                  <ListIcon className="w-3.5 h-3.5" />
                  <span className="font-bold">列表视图</span>
                </TabsTrigger>
              </TabsList>
            </div>

            <div className="flex-1 min-h-0 mt-2">
              <TabsContent value="grid" className="h-full m-0">
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 pb-4 overflow-y-auto h-full pr-2 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-slate-800">
                  {Object.entries(groupedHoldings).map(([sector, items]) => (
                    <SectorHoldingCard
                      key={sector}
                      sectorName={sector}
                      holdings={items}
                    />
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="list" className="h-full m-0">
                <div className="h-full rounded-2xl border border-slate-200/60 dark:border-white/5 overflow-hidden shadow-sm bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl">
                  <HoldingsDataList holdings={MOCK_HOLDINGS_DATA} />
                </div>
              </TabsContent>
            </div>
          </Tabs>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

import React, { useEffect, useState } from 'react';
import { useLocation } from 'wouter';

import { useDeploymentSync } from '@/hooks/useDeploymentSync';

import { ChinaIndicesManager } from '../components/ChinaIndicesManager';
import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { ExRightsDataManager } from '../components/ExRightsDataManager';
import { MarketActionBar } from '../components/MarketActionBar';
import { MarketOverview } from '../components/MarketOverview';
import { MarketSidebar } from '../components/MarketSidebar';
import { MarketStatsCards } from '../components/MarketStatsCards';
import { StockDataQueryCard } from '../components/SingleStockSyncCard';
import { MARKET_MENU_ITEMS } from '../constants/marketMenu';

const DEFAULT_MARKET_TAB = 'overview';
const marketTabIds = new Set<string>(MARKET_MENU_ITEMS.map(item => item.id));

function getMarketTabFromLocation(location: string) {
  const search = location.includes('?')
    ? location.slice(location.indexOf('?') + 1)
    : typeof window !== 'undefined'
      ? window.location.search
      : '';
  const tab = new URLSearchParams(search).get('tab');

  return tab && marketTabIds.has(tab) ? tab : DEFAULT_MARKET_TAB;
}

function getMarketTabPath(tab: string) {
  return tab === DEFAULT_MARKET_TAB
    ? '/settings/data/market'
    : `/settings/data/market?tab=${tab}`;
}

export function ComprehensiveMarketPage() {
  const [location, setLocation] = useLocation();
  const [activeTab, setActiveTab] = useState(() =>
    getMarketTabFromLocation(location)
  );

  // 使用统一的 Hook 管理部署同步
  const marketSync = useDeploymentSync('market-sync', {
    successMessage: '全市场同步已启动',
  });
  // Mock Market Stats (Placeholder until real stats API is ready)
  const mockStats = {
    totalStocks: 5243,
    dataCoverage: 98.5,
    marketVolume: 8942,
    latency: 45,
  };

  useEffect(() => {
    setActiveTab(getMarketTabFromLocation(location));
  }, [location]);

  const handleTabChange = (tab: string) => {
    const nextTab = marketTabIds.has(tab) ? tab : DEFAULT_MARKET_TAB;
    setActiveTab(nextTab);
    setLocation(getMarketTabPath(nextTab));
  };

  return (
    <DataStudioPageFrame
      activeMode="MARKET"
      description="行情、指数、市场概览"
      title="全市场数据"
    >
      <div className="flex h-full min-h-0 flex-col gap-ui-panel animate-fade-in">
        {/* Action Bar */}
        <MarketActionBar
          activeTab={activeTab}
          sync={marketSync}
          onBack={() => setLocation('/settings/data')}
        />

        {/* Layout Grid */}
        <div className="flex flex-col lg:flex-row gap-ui-panel flex-1 min-h-0">
          {/* Left Sidebar */}
          <MarketSidebar activeTab={activeTab} onTabChange={handleTabChange} />

          {/* Right Content Area */}
          <div className="flex-1 flex flex-col gap-ui-panel min-h-0">
            {activeTab === 'overview' && (
              <>
                <MarketStatsCards stats={mockStats} />
                <MarketOverview />
              </>
            )}

            {activeTab === 'ex-rights' && <ExRightsDataManager />}

            {activeTab === 'indices' && <ChinaIndicesManager />}

            {activeTab === 'stocks' && (
              <div className="grid h-full min-h-[520px] grid-cols-1 gap-ui-panel xl:grid-cols-[360px_minmax(0,1fr)]">
                <div className="h-[360px]">
                  <StockDataQueryCard />
                </div>
                <div className="flex h-full flex-col justify-center rounded-panel border border-slate-200 bg-white/60 p-ui-section shadow-sm dark:border-white/5 dark:bg-white/[0.02]">
                  <div className="max-w-2xl">
                    <h3 className="text-ui-page-title font-black text-slate-900 dark:text-white">
                      个股数据资产
                    </h3>
                    <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                      {['基础资料', 'K线缓存', '财务四表'].map(item => (
                        <div
                          key={item}
                          className="rounded-panel border border-slate-200 bg-slate-50 p-ui-section dark:border-slate-800 dark:bg-slate-900/60"
                        >
                          <div className="text-ui-caption font-black uppercase tracking-widest text-slate-400">
                            DATA LAYER
                          </div>
                          <div className="mt-1 text-ui-body font-black text-slate-800 dark:text-slate-100">
                            {item}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab !== 'overview' &&
              activeTab !== 'ex-rights' &&
              activeTab !== 'indices' &&
              activeTab !== 'stocks' && (
                <div className="h-full w-full flex flex-col items-center justify-center bg-white/50 dark:bg-white/[0.02] rounded-panel border border-slate-200 dark:border-white/5 backdrop-blur-sm">
                  <div className="text-center p-ui-section">
                    <div className="w-16 h-16 bg-slate-100 dark:bg-white/5 rounded-full flex items-center justify-center mx-auto mb-4">
                      <span className="text-ui-display">🚧</span>
                    </div>
                    <h3 className="text-ui-heading font-bold text-slate-900 dark:text-white mb-2">
                      {activeTab} 模块开发中
                    </h3>
                    <p className="text-slate-500 dark:text-slate-400 max-w-sm">
                      该功能模块正在积极开发中，将集成更多详细的市场数据分析功能。
                    </p>
                  </div>
                </div>
              )}
          </div>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

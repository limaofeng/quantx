import React from 'react';

import { DataPortalHeader } from '../components/DataPortalHeader';
import { FinancialDataSyncCard } from '../components/FinancialDataSyncCard';
import { GlobalSyncCard } from '../components/GlobalSyncCard';
import { HoldingsSummaryCard } from '../components/HoldingsSummaryCard';
import { ReverseRepoSyncCard } from '../components/ReverseRepoSyncCard';
import { SectorSyncCard } from '../components/SectorSyncCard';
import { StockDataQueryCard } from '../components/SingleStockSyncCard';
import { SyncStatusWidget } from '../components/SyncStatusWidget';
import { SystemInsightCard } from '../components/SystemInsightCard';
import { TradingCalendarCard } from '../components/TradingCalendarCard';
import { TransactionDataSyncCard } from '../components/TransactionDataSyncCard';

// Mock Data
const MOCK_HOLDINGS = Array(15)
  .fill(null)
  .map((_, i) => ({
    name: i % 2 === 0 ? '贵州茅台' : '宁德时代',
    code: i % 2 === 0 ? '600519.SH' : '300750.SZ',
    price: '1680.50',
    changePercent: '+1.2%',
    status: i < 12 ? 'success' : 'pending',
    completeness: 98,
    lastSync: '10 mins ago',
  }));

export function DataManagementPage() {
  return (
    <div className="container mx-auto max-w-[1600px] space-y-6">
      {/* Header Section */}
      <div className="flex items-end justify-between border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-black tracking-tight text-slate-900 dark:text-white flex items-center gap-2">
            <span className="w-2 h-8 rounded bg-indigo-600 inline-block"></span>
            数据管理门户
          </h1>
          <p className="text-slate-500 dark:text-slate-400 mt-2 text-sm ml-4">
            统筹管理全市场行情数据同步、本地缓存与系统健康状态。
          </p>
        </div>
        <div className="pb-1">
          <DataPortalHeader />
        </div>
      </div>

      {/* Main Layout (Grid) */}
      <div className="grid grid-cols-12 gap-6">
        {/* Left Column: Management Tiles (9 cols) */}
        <div className="col-span-12 lg:col-span-9 flex flex-col gap-6">
          {/* Global Insight Section */}
          <SystemInsightCard />

          {/* Action Tiles Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-9 gap-6">
            <div className="md:col-span-2 lg:col-span-9 min-h-[160px]">
              <GlobalSyncCard />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[200px]">
              <SectorSyncCard />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[200px]">
              <TradingCalendarCard />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[200px]">
              <HoldingsSummaryCard holdings={MOCK_HOLDINGS} />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[160px]">
              <ReverseRepoSyncCard />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[160px]">
              <FinancialDataSyncCard />
            </div>
            <div className="md:col-span-1 lg:col-span-3 min-h-[160px]">
              <TransactionDataSyncCard />
            </div>
          </div>
        </div>

        {/* Right Column: Query & Status (3 cols) */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-6 h-full min-h-[500px]">
          <div className="h-[200px] shrink-0">
            <StockDataQueryCard />
          </div>
          <div className="flex-1 min-h-[300px]">
            <SyncStatusWidget />
          </div>
        </div>
      </div>
    </div>
  );
}

import { DataPortalHeader } from '../components/DataPortalHeader';
import { DataStudioShell } from '../components/DataStudioShell';
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
    <DataStudioShell
      activeMode="OVERVIEW"
      className="h-full min-h-0"
      showSidebar={false}
      content={
        <div className="h-full overflow-y-auto bg-[#08101d] p-3 custom-scrollbar">
          <div className="grid min-h-full grid-cols-12 gap-3">
            <div className="col-span-12 flex flex-col gap-3 xl:col-span-9">
              <SystemInsightCard />

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-9">
                <div className="min-h-[150px] md:col-span-2 xl:col-span-9">
                  <GlobalSyncCard />
                </div>
                <div className="min-h-[180px] xl:col-span-3">
                  <SectorSyncCard />
                </div>
                <div className="min-h-[180px] xl:col-span-3">
                  <TradingCalendarCard />
                </div>
                <div className="min-h-[180px] xl:col-span-3">
                  <HoldingsSummaryCard holdings={MOCK_HOLDINGS} />
                </div>
                <div className="min-h-[150px] xl:col-span-3">
                  <ReverseRepoSyncCard />
                </div>
                <div className="min-h-[150px] xl:col-span-3">
                  <FinancialDataSyncCard />
                </div>
                <div className="min-h-[150px] xl:col-span-3">
                  <TransactionDataSyncCard />
                </div>
              </div>
            </div>

            <div className="col-span-12 flex min-h-[500px] flex-col gap-3 xl:col-span-3">
              <div className="h-[190px] shrink-0">
                <StockDataQueryCard />
              </div>
              <div className="min-h-[300px] flex-1">
                <SyncStatusWidget />
              </div>
            </div>
          </div>
        </div>
      }
      tabBarTrailing={<DataPortalHeader />}
    />
  );
}

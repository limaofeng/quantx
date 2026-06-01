import { Search } from 'lucide-react';
import React, { useState, useMemo } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Input } from '@/components/ui/input';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';

import { gql } from '../../../generated/gql';
import { SectorActionBar } from '../components/SectorActionBar';
import { SectorDetailDrawer } from '../components/SectorDetailDrawer';
import { SectorSidebar } from '../components/SectorSidebar';
import { SectorStatsCards } from '../components/SectorStatsCards';
import { SectorTable } from '../components/SectorTable';
import { TaskHistory } from '../components/TaskHistory';

// Define the GraphQL queries (仅保留业务相关的)
const GET_SECTORS = gql(`
  query GetSectors($classification: String, $search: String, $limit: Int, $offset: Int) {
    sectors(classification: $classification, search: $search, limit: $limit, offset: $offset) {
      items {
        id
        name
        code
        classification
        market
        description
        level
        parentId
        stockCodes
      }
      total
    }
  }
`);

const GET_SECTOR_STATS = gql(`
  query GetSectorStats {
    sectorStats {
      classification
      count
    }
  }
`);

export function SectorDataPage() {
  const [, setLocation] = useLocation();
  const [activeTab, setActiveTab] = useState('SW1');
  const [searchQuery, setSearchQuery] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(100);
  const [selectedSector, setSelectedSector] = useState<any>(null);
  const [showHistory, setShowHistory] = useState(false);

  // 使用统一的 Hook 管理部署同步
  const {
    deployment: sectorSyncDeployment,
    isSyncing,
    triggerSync,
  } = useDeploymentSync('sector-data-sync', { successMessage: '同步已启动' });

  // Fetch Stats once
  const [{ data: statsData }] = useQuery({ query: GET_SECTOR_STATS as any });

  // Fetch paginated data
  const [{ data, fetching }] = useQuery({
    query: GET_SECTORS as any,
    variables: {
      classification: activeTab === 'all' ? null : activeTab,
      search: searchQuery || null,
      limit: pageSize,
      offset: (currentPage - 1) * pageSize,
    },
  });

  const sectors = data?.sectors?.items || [];
  const totalCount = data?.sectors?.total || 0;
  const totalPages = Math.ceil(totalCount / pageSize);

  // Stats from backend
  const statsCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    (statsData?.sectorStats as any[])?.forEach((s: any) => {
      counts[s.classification] = s.count;
    });
    return counts;
  }, [statsData]);

  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
    setCurrentPage(1);
  };

  const handleSearchChange = (val: string) => {
    setSearchQuery(val);
    setCurrentPage(1);
  };

  return (
    <>
      <div className="flex flex-col gap-6 animate-fade-in -mt-4 h-[calc(100vh-var(--header-height)-3rem)]">
        {/* Action Bar & Identity Area */}
        <SectorActionBar
          activeTab={activeTab}
          totalCount={totalCount}
          currentPage={currentPage}
          totalPages={totalPages}
          sectorSyncDeployment={sectorSyncDeployment}
          isSyncing={isSyncing}
          onBack={() => setLocation('/settings/data')}
          onShowHistory={() => setShowHistory(true)}
          onSync={triggerSync}
        />

        {/* Layout Grid */}
        <div className="flex flex-col lg:flex-row gap-6 flex-1 min-h-0">
          {/* Left Sub-Navigation */}
          <SectorSidebar
            activeTab={activeTab}
            statsCounts={statsCounts}
            onTabChange={handleTabChange}
          />

          {/* Right Content Column */}
          <div className="flex-1 flex flex-col gap-6 min-h-0">
            {/* Summary Stats Row */}
            <SectorStatsCards
              totalCount={totalCount}
              statsCounts={statsCounts}
            />

            {/* Search and Table Area */}
            <div className="flex flex-col gap-4 flex-1 min-h-0">
              <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-md group">
                  <Input
                    placeholder="搜索板块名称、代码或描述..."
                    className="pl-10 h-10 rounded-2xl bg-white/50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 focus-visible:ring-primary/20 focus-visible:border-primary transition-all shadow-sm backdrop-blur-md"
                    value={searchQuery}
                    onChange={e => handleSearchChange(e.target.value)}
                  />
                  <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 group-focus-within:text-primary transition-colors z-10 pointer-events-none" />
                </div>
                <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest hidden sm:block">
                  共发现 {totalCount} 个板块
                </div>
              </div>

              <SectorTable
                sectors={sectors}
                fetching={fetching}
                totalCount={totalCount}
                currentPage={currentPage}
                pageSize={pageSize}
                onPageChange={setCurrentPage}
                onSectorClick={setSelectedSector}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Task History Component */}
      <TaskHistory
        open={showHistory}
        onOpenChange={setShowHistory}
        deploymentId={sectorSyncDeployment?.id}
        deploymentName={sectorSyncDeployment?.flowName}
        workPoolName={sectorSyncDeployment?.workPoolName}
      />

      {/* Detail Drawer */}
      <SectorDetailDrawer
        sector={selectedSector}
        open={!!selectedSector}
        onOpenChange={open => !open && setSelectedSector(null)}
      />
    </>
  );
}

import { ArrowLeft } from 'lucide-react';
import React, { useState } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { gql } from '@/generated/gql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { SyncControlPanel } from '../components/SyncControlPanel';
import { TaskHistory } from '../components/TaskHistory';
import { TradingCalendar } from '../components/TradingCalendar';

const GET_HOLIDAYS_COUNT = gql(`
  query GetHolidaysCountPage($market: String!, $year: Int!) {
    holidays(market: $market, year: $year) {
      total
    }
  }
`);

export function TradingCalendarPage() {
  const [, setLocation] = useLocation();
  const [showHistory, setShowHistory] = useState(false);
  const currentYear = new Date().getFullYear();

  // Fetch Holidays Count for Header
  const [{ data: holidaysData }] = useQuery({
    query: GET_HOLIDAYS_COUNT as any,
    variables: { market: 'SH', year: currentYear },
  });

  const { deployment, isSyncing, triggerSync } = useDeploymentSync(
    'holiday-sync',
    {
      successMessage: '交易日历同步任务已提交',
    }
  );

  const holidayCount = holidaysData?.holidays?.total ?? 0;

  return (
    <DataStudioPageFrame
      activeMode="CALENDAR"
      description="交易日、休市、开盘状态"
      title="交易日历"
    >
      <div className="flex flex-col gap-4 animate-fade-in -mt-2 h-[calc(100vh-var(--header-height)-2rem)]">
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
                交易日历
              </h1>
              <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-80">
                {currentYear} 年度 • {holidayCount} 个节假日
              </p>
            </div>
          </div>

          <SyncControlPanel
            deployment={deployment}
            isSyncing={isSyncing}
            defaultFlowName="交易日历同步"
            onShowHistory={() => setShowHistory(true)}
            onSync={triggerSync}
          />
        </div>

        {/* Main Content */}
        <div className="flex-1 min-h-0">
          <TradingCalendar hideSyncWidget={true} />
        </div>
      </div>

      {/* Task History Dialog */}
      <TaskHistory
        open={showHistory}
        onOpenChange={setShowHistory}
        deploymentId={deployment?.id}
        deploymentName={deployment?.flowName || '交易日历同步'}
        workPoolName={deployment?.workPoolName}
      />
    </DataStudioPageFrame>
  );
}

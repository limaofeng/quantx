import { ArrowLeft } from 'lucide-react';
import React from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { gql } from '@/generated/gql';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';
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
  const currentYear = new Date().getFullYear();

  // Fetch Holidays Count for Header
  const [{ data: holidaysData }] = useQuery({
    query: GET_HOLIDAYS_COUNT,
    variables: { market: 'SH', year: currentYear },
  });

  const holidayCount = holidaysData?.holidays?.total ?? 0;

  return (
    <DataStudioPageFrame
      activeMode="CALENDAR"
      description="交易日、休市、开盘状态"
      title="交易日历"
    >
      <div className="flex h-full min-h-0 flex-col gap-4 animate-fade-in">
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

          <DeploymentSyncControl
            deploymentName="holiday-sync"
            defaultFlowName="交易日历同步"
            successMessage="交易日历同步任务已提交"
          />
        </div>

        {/* Main Content */}
        <div className="flex-1 min-h-0">
          <TradingCalendar hideSyncWidget={true} />
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

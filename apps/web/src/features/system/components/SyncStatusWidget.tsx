import { Activity, RotateCw, StopCircle, Rocket, Clock } from 'lucide-react';
import React, { useState, useEffect } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';

// Types based on Prefect concepts
interface Deployment {
  id: string;
  name: string;
  flowName: string;
  schedule: string;
  lastRunStatus: 'success' | 'failed' | 'pending' | 'running';
  lastRunTime: string;
  nextRunTime: string;
}

interface FlowRun {
  id: string;
  name: string;
  state: 'Running' | 'Completed' | 'Failed' | 'Pending' | 'Scheduled';
  startTime: string;
  duration?: string;
  deploymentName?: string;
}

export function SyncStatusWidget() {
  // Mock Deployments
  const [deployments] = useState<Deployment[]>([
    {
      id: 'd1',
      name: 'batch-stock-sync',
      flowName: '批量股票同步',
      schedule: '08:00 (工作日)',
      lastRunStatus: 'success',
      lastRunTime: '08:00:00',
      nextRunTime: '明天 08:00',
    },
    {
      id: 'd2',
      name: 'daily-trading-sync',
      flowName: '每日交易同步',
      schedule: '15:30 (工作日)',
      lastRunStatus: 'success',
      lastRunTime: '15:30:00',
      nextRunTime: '16:00',
    },
    {
      id: 'd3',
      name: 'daily-market-data-sync',
      flowName: '每日市场行情',
      schedule: '16:30 (工作日)',
      lastRunStatus: 'running',
      lastRunTime: '16:30:00',
      nextRunTime: '明天 12:30',
    },
    {
      id: 'd4',
      name: 'bond-repo-auto-trade',
      flowName: '国债逆回购',
      schedule: '15:10 (工作日)',
      lastRunStatus: 'success',
      lastRunTime: '15:10:00',
      nextRunTime: '明天 15:10',
    },
  ]);

  // Mock Active & Recent Flow Runs
  const [flowRuns, setFlowRuns] = useState<FlowRun[]>([
    {
      id: 'fr-1',
      name: 'daily-market-data-sync/manual',
      state: 'Running',
      startTime: '16:30:05',
      deploymentName: 'daily-market-data-sync',
    },
    {
      id: 'fr-2',
      name: 'stock_sync_flow/600519.SH',
      state: 'Completed',
      startTime: '14:20:00',
      duration: '2.5s',
    },
    {
      id: 'fr-3',
      name: 'stock_sync_flow/000001.SZ',
      state: 'Completed',
      startTime: '14:19:55',
      duration: '1.8s',
    },
    {
      id: 'fr-4',
      name: 'batch-holding-stock-data-sync',
      state: 'Failed',
      startTime: '15:30:00',
      duration: '45s',
      deploymentName: 'batch-holding-stock-data-sync',
    },
  ]);

  // Simulate updates
  useEffect(() => {
    const interval = setInterval(() => {
      setFlowRuns(prev =>
        prev.map(run => {
          if (run.state === 'Running' && Math.random() > 0.9) {
            return { ...run, state: 'Completed', duration: '125s' };
          }
          return run;
        })
      );
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col h-full rounded-xl border border-slate-200/40 dark:border-slate-800/40 bg-slate-50/40 dark:bg-slate-900/40 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-3 border-b border-slate-200/40 dark:border-slate-800/40 bg-slate-100/40 dark:bg-slate-900/40">
        <div className="flex items-center gap-2">
          <Rocket className="w-4 h-4 text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-700 dark:text-slate-200">
            任务调度
          </h3>
        </div>
        <Badge
          variant="outline"
          className="font-mono text-[10px] text-slate-500 border-slate-200/60 dark:border-slate-700/60"
        >
          Prefect
        </Badge>
      </div>

      <ScrollArea className="flex-1">
        <div className="flex flex-col p-2 space-y-4">
          {/* Active Section */}
          <div>
            <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2 px-2 flex items-center gap-1">
              <Activity className="w-3 h-3" /> 正在执行
            </h4>
            <div className="space-y-1">
              {flowRuns.filter(r => r.state === 'Running').length === 0 ? (
                <div className="px-2 py-1 text-[10px] text-slate-400 italic">
                  No active tasks
                </div>
              ) : (
                flowRuns
                  .filter(r => r.state === 'Running')
                  .map(run => (
                    <div
                      key={run.id}
                      className="flex flex-col gap-1 p-2 rounded bg-white/60 dark:bg-slate-800/60 border border-indigo-100 dark:border-indigo-900/30"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-xs text-slate-700 dark:text-slate-200 truncate">
                          {run.name}
                        </span>
                        <RotateCw className="w-3 h-3 text-indigo-500 animate-spin" />
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-slate-400">
                        <span className="font-mono">{run.startTime}</span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-4 w-4 hover:text-red-500 text-slate-400"
                        >
                          <StopCircle className="w-3 h-3" />
                        </Button>
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>

          {/* Deployments */}
          <div>
            <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2 px-2 flex items-center gap-1">
              <Clock className="w-3 h-3" /> 定时任务
            </h4>
            <div className="space-y-1">
              {deployments.map(dep => (
                <div
                  key={dep.id}
                  className="flex flex-col gap-1 p-2 rounded hover:bg-white/40 dark:hover:bg-slate-800/40 transition-colors border border-transparent hover:border-slate-100 dark:hover:border-slate-800"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                      {dep.name}
                    </span>
                    {dep.lastRunStatus === 'success' && (
                      <div
                        className="w-1.5 h-1.5 rounded-full bg-emerald-500"
                        title="Success"
                      />
                    )}
                    {dep.lastRunStatus === 'failed' && (
                      <div
                        className="w-1.5 h-1.5 rounded-full bg-red-500"
                        title="Failed"
                      />
                    )}
                    {dep.lastRunStatus === 'running' && (
                      <div
                        className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse"
                        title="Running"
                      />
                    )}
                  </div>
                  <div className="flex items-center justify-between text-[10px] text-slate-400">
                    <span>{dep.schedule}</span>
                    <span className="font-mono">
                      Next: {dep.nextRunTime.split(' ')[1] || dep.nextRunTime}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}

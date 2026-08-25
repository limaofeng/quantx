import { format } from 'date-fns';
import {
  Activity,
  Clock,
  Eye,
  RefreshCw,
  StopCircle,
  Timer,
} from 'lucide-react';
import React from 'react';
import { useMutation, useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

const GET_DEPLOYMENT_RUN_MONITOR = `
  query DeploymentRunMonitor($deploymentId: String!, $limit: Int, $offset: Int) {
    flowRuns(deploymentId: $deploymentId, limit: $limit, offset: $offset) {
      items {
        id
        flowName
        state
        startedAt
        expectedStartTime
        created
        totalRunTime
        parameters
      }
      total
    }
  }
`;

const CANCEL_FLOW_RUN = `
  mutation CancelFlowRunFromMonitor($runId: String!) {
    cancelFlowRun(runId: $runId) {
      success
      message
      data
    }
  }
`;

interface FlowRunItem {
  id: string;
  flowName: string;
  state: string;
  startedAt?: string | null;
  expectedStartTime?: string | null;
  created?: string | null;
  totalRunTime?: number | null;
  parameters?: string | null;
}

interface FlowRunsData {
  flowRuns?: {
    items: FlowRunItem[];
    total: number;
  };
}

interface FlowRunsVars {
  deploymentId: string;
  limit: number;
  offset: number;
}

interface CancelFlowRunData {
  cancelFlowRun?: {
    success: boolean;
    message?: string | null;
    data?: string | null;
  };
}

interface CancelFlowRunVars {
  runId: string;
}

const activeStates = new Set([
  'LATE',
  'PENDING',
  'RUNNING',
  'SCHEDULED',
  'CANCELLING',
]);

function normalizeState(state: string | null | undefined) {
  return (state || '').toUpperCase();
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return format(date, 'MM-dd HH:mm');
}

function getParameterCount(parameters: string | null | undefined) {
  if (!parameters) return 0;
  try {
    return Object.keys(JSON.parse(parameters)).length;
  } catch {
    return 0;
  }
}

function getStateStyle(state: string) {
  switch (normalizeState(state)) {
    case 'COMPLETED':
      return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-500';
    case 'FAILED':
    case 'CRASHED':
      return 'border-red-500/20 bg-red-500/10 text-red-500';
    case 'RUNNING':
      return 'border-blue-500/20 bg-blue-500/10 text-blue-500';
    case 'PENDING':
    case 'SCHEDULED':
    case 'LATE':
      return 'border-amber-500/20 bg-amber-500/10 text-amber-500';
    case 'CANCELLED':
      return 'border-slate-500/20 bg-slate-500/10 text-slate-500';
    default:
      return 'border-slate-500/20 bg-slate-500/10 text-slate-500';
  }
}

interface DeploymentRunMonitorProps {
  deploymentId?: string;
  deploymentName: string;
  title?: string;
}

export function DeploymentRunMonitor({
  deploymentId,
  deploymentName,
  title = '运行监控',
}: DeploymentRunMonitorProps) {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [{ data, fetching }, reexecute] = useQuery<FlowRunsData, FlowRunsVars>({
    query: GET_DEPLOYMENT_RUN_MONITOR,
    variables: {
      deploymentId: deploymentId || '',
      limit: 12,
      offset: 0,
    },
    pause: !deploymentId,
    requestPolicy: 'cache-and-network',
  });
  const [cancelResult, cancelFlowRun] = useMutation<
    CancelFlowRunData,
    CancelFlowRunVars
  >(CANCEL_FLOW_RUN);

  const runs = data?.flowRuns?.items ?? [];
  const activeRuns = runs.filter(run =>
    activeStates.has(normalizeState(run.state))
  );
  const recentRuns = runs
    .filter(run => !activeStates.has(normalizeState(run.state)))
    .slice(0, 4);

  const handleCancel = async (runId: string) => {
    const result = await cancelFlowRun({ runId });
    const payload = result.data?.cancelFlowRun;

    if (result.error || !payload?.success) {
      toast({
        title: '取消失败',
        description: result.error?.message || payload?.message || runId,
        variant: 'destructive',
      });
      return;
    }

    toast({
      title: '已提交取消',
      description: payload.message || runId,
      variant: 'success',
    });
    reexecute({ requestPolicy: 'network-only' });
  };

  return (
    <div className="flex h-full min-h-[320px] flex-col rounded-panel border border-slate-200/60 bg-white/70 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
      <div className="flex items-center justify-between border-b border-slate-200/60 px-ui-section py-3 dark:border-white/5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="rounded-lg bg-blue-500/10 p-2 text-blue-600 dark:text-blue-400">
            <Activity className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-ui-body font-black text-slate-900 dark:text-white">
              {title}
            </h2>
            <p className="truncate font-mono text-ui-caption font-bold uppercase tracking-widest text-slate-400">
              {deploymentName}
            </p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-control-compact gap-1.5 rounded-lg text-ui-caption font-bold"
          disabled={!deploymentId || fetching}
          onClick={() => reexecute({ requestPolicy: 'network-only' })}
        >
          <RefreshCw
            className={cn('h-3.5 w-3.5', fetching && 'animate-spin')}
          />
          刷新
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-ui-section overflow-y-auto p-ui-section custom-scrollbar">
        {!deploymentId ? (
          <div className="flex h-44 items-center justify-center rounded-lg border border-dashed border-slate-200 text-ui-label font-medium text-slate-400 dark:border-white/10">
            未找到 deployment
          </div>
        ) : fetching && runs.length === 0 ? (
          <div className="flex h-44 flex-col items-center justify-center gap-3 text-ui-label font-medium text-slate-400">
            <RefreshCw className="h-6 w-6 animate-spin text-blue-500" />
            正在加载运行记录
          </div>
        ) : (
          <>
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-ui-caption font-black uppercase tracking-[0.22em] text-slate-500">
                  Active Runs
                </h3>
                <Badge
                  variant="outline"
                  className="border-blue-500/20 bg-blue-500/10 text-ui-caption text-blue-500"
                >
                  {activeRuns.length}
                </Badge>
              </div>

              {activeRuns.length === 0 ? (
                <div className="rounded-lg border border-slate-200/60 bg-slate-50/80 p-ui-section text-ui-label font-medium text-slate-500 dark:border-white/5 dark:bg-white/[0.02] dark:text-slate-400">
                  当前没有运行中或等待中的任务。
                </div>
              ) : (
                <div className="space-y-2">
                  {activeRuns.map(run => (
                    <div
                      key={run.id}
                      className="rounded-lg border border-blue-500/20 bg-blue-500/[0.04] p-3"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="h-2 w-2 rounded-full bg-blue-500" />
                            <p className="truncate text-ui-label font-black text-slate-900 dark:text-white">
                              {run.flowName || deploymentName}
                            </p>
                          </div>
                          <p className="mt-1 font-mono text-ui-caption text-slate-500">
                            {run.id}
                          </p>
                        </div>
                        <Badge
                          variant="outline"
                          className={cn(
                            'shrink-0 text-ui-caption',
                            getStateStyle(run.state)
                          )}
                        >
                          {run.state}
                        </Badge>
                      </div>

                      <div className="mt-3 grid grid-cols-2 gap-2 text-ui-caption text-slate-500 sm:grid-cols-3">
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="h-3 w-3" />
                          {formatDateTime(
                            run.startedAt || run.expectedStartTime
                          )}
                        </span>
                        <span className="inline-flex items-center gap-1.5">
                          <Timer className="h-3 w-3" />
                          参数 {getParameterCount(run.parameters)}
                        </span>
                        <span className="font-mono">{run.id.slice(0, 8)}</span>
                      </div>

                      <div className="mt-3 flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-control-compact gap-1.5 rounded-lg text-ui-caption font-bold"
                          onClick={() =>
                            setLocation(`/system/flow-runs/${run.id}`)
                          }
                        >
                          <Eye className="h-3.5 w-3.5" />
                          详情
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          className="h-control-compact gap-1.5 rounded-lg text-ui-caption font-bold"
                          disabled={cancelResult.fetching}
                          onClick={() => void handleCancel(run.id)}
                        >
                          <StopCircle className="h-3.5 w-3.5" />
                          取消
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-ui-caption font-black uppercase tracking-[0.22em] text-slate-500">
                Recent Runs
              </h3>
              {recentRuns.length === 0 ? (
                <div className="rounded-lg border border-slate-200/60 bg-slate-50/80 p-ui-section text-ui-label font-medium text-slate-500 dark:border-white/5 dark:bg-white/[0.02] dark:text-slate-400">
                  暂无最近完成记录。
                </div>
              ) : (
                <div className="divide-y divide-slate-200/60 overflow-hidden rounded-lg border border-slate-200/60 dark:divide-white/5 dark:border-white/5">
                  {recentRuns.map(run => (
                    <button
                      key={run.id}
                      type="button"
                      className="flex w-full items-center justify-between gap-3 bg-white/60 px-3 py-2.5 text-left transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 dark:bg-white/[0.02] dark:hover:bg-white/[0.04]"
                      onClick={() => setLocation(`/system/flow-runs/${run.id}`)}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-ui-label font-bold text-slate-800 dark:text-slate-100">
                          {run.flowName || deploymentName}
                        </p>
                        <p className="font-mono text-ui-caption text-slate-500">
                          {formatDateTime(run.startedAt || run.created)}
                        </p>
                      </div>
                      <Badge
                        variant="outline"
                        className={cn(
                          'shrink-0 text-ui-caption',
                          getStateStyle(run.state)
                        )}
                      >
                        {run.state}
                      </Badge>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}

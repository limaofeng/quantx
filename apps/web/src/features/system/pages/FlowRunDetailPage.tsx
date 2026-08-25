import { format } from 'date-fns';
import {
  ArrowLeft,
  Clock,
  Terminal,
  Activity,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Timer,
} from 'lucide-react';
import React from 'react';
import { useQuery } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';

// 定义 GraphQL 查询
const GET_FLOW_RUN_DETAIL = `
  query GetFlowRunDetail($id: String!) {
    flowRun(runId: $id) {
      id
      flowName
      state
      startedAt
      finishedAt
      totalRunTime
      parameters
      taskRuns {
        id
        name
        state
        startedAt
        finishedAt
        totalRunTime
        taskInputs
      }
      detailedLogs {
        time
        level
        message
      }
    }
  }
`;

interface FlowTaskRun {
  id: string;
  name: string;
  state: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  taskInputs?: unknown;
}

interface FlowDetailedLog {
  time: string;
  level: number;
  message: string;
}

function formatParameters(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value ?? {}, null, 2);
}

interface FlowRunDetailQuery {
  flowRun?: {
    id: string;
    flowName: string;
    state: string;
    startedAt?: string | null;
    finishedAt?: string | null;
    totalRunTime?: number | null;
    parameters?: unknown;
    taskRuns: FlowTaskRun[];
    detailedLogs: FlowDetailedLog[];
  } | null;
}

export function FlowRunDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;

  const [{ data, fetching, error }] = useQuery<
    FlowRunDetailQuery,
    { id: string }
  >({
    query: GET_FLOW_RUN_DETAIL,
    variables: { id },
    pause: !id,
  });

  if (fetching)
    return (
      <DataStudioPageFrame
        activeMode="OVERVIEW"
        description="同步任务详情与日志"
        title="任务详情"
      >
        <div className="flex min-h-[320px] items-center justify-center text-ui-body text-slate-500">
          Loading...
        </div>
      </DataStudioPageFrame>
    );
  if (error)
    return (
      <DataStudioPageFrame
        activeMode="OVERVIEW"
        description="同步任务详情与日志"
        title="任务详情"
      >
        <div className="rounded-lg border border-rose-500/20 bg-rose-500/10 p-ui-section text-ui-body text-rose-300">
          Error: {error.message}
        </div>
      </DataStudioPageFrame>
    );
  if (!data?.flowRun)
    return (
      <DataStudioPageFrame
        activeMode="OVERVIEW"
        description="同步任务详情与日志"
        title="任务详情"
      >
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-ui-section text-ui-body text-slate-500">
          Flow run not found
        </div>
      </DataStudioPageFrame>
    );

  const { flowRun } = data;

  // 状态颜色映射
  const getStateColor = (state: string) => {
    switch (state) {
      case 'COMPLETED':
        return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20';
      case 'FAILED':
        return 'bg-red-500/10 text-red-500 border-red-500/20';
      case 'RUNNING':
        return 'bg-blue-500/10 text-blue-500 border-blue-500/20';
      case 'PENDING':
        return 'bg-amber-500/10 text-amber-500 border-amber-500/20';
      case 'CANCELLED':
        return 'bg-slate-500/10 text-slate-500 border-slate-500/20';
      default:
        return 'bg-slate-100 text-slate-600 border-slate-200';
    }
  };

  const getStateIcon = (state: string) => {
    switch (state) {
      case 'COMPLETED':
        return <CheckCircle2 size={16} />;
      case 'FAILED':
        return <XCircle size={16} />;
      case 'RUNNING':
        return <Activity size={16} className="animate-spin" />;
      default:
        return <AlertCircle size={16} />;
    }
  };

  return (
    <DataStudioPageFrame
      activeMode="OVERVIEW"
      description="同步任务详情与日志"
      title={flowRun.flowName}
    >
      <div className="flex flex-col h-full gap-ui-panel p-ui-panel animate-fade-in">
        {/* Header */}
        <div className="flex items-center gap-ui-section">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => window.history.back()}
          >
            <ArrowLeft size={20} />
          </Button>
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-1">
              <h1 className="text-ui-display font-bold tracking-tight">
                {flowRun.flowName}
              </h1>
              <Badge
                variant="outline"
                className={cn(
                  'px-2.5 py-0.5 font-mono text-ui-label font-bold border',
                  getStateColor(flowRun.state)
                )}
              >
                {getStateIcon(flowRun.state)}
                <span className="ml-1.5">{flowRun.state}</span>
              </Badge>
            </div>
            <div className="flex items-center gap-ui-section text-ui-body text-slate-500">
              <span className="font-mono text-ui-label opacity-60">
                ID: {flowRun.id}
              </span>
              <span className="flex items-center gap-1.5">
                <Clock size={14} />
                {flowRun.startedAt
                  ? format(new Date(flowRun.startedAt), 'yyyy-MM-dd HH:mm:ss')
                  : '-'}
              </span>
              {flowRun.totalRunTime && (
                <span className="flex items-center gap-1.5">
                  <Timer size={14} />
                  {flowRun.totalRunTime.toFixed(2)}s
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-ui-panel flex-1 min-h-0">
          {/* Left: Task Timeline & List */}
          <Card className="lg:col-span-2 flex flex-col border-slate-200/60 shadow-sm overflow-hidden">
            <CardHeader className="py-ui-section px-ui-panel border-b bg-slate-50/50">
              <CardTitle className="text-ui-body font-bold uppercase tracking-wider text-slate-500">
                Task Execution
              </CardTitle>
            </CardHeader>
            <ScrollArea className="flex-1 p-0">
              <div className="divide-y divide-slate-100">
                {flowRun.taskRuns.length === 0 ? (
                  <div className="p-ui-section text-center text-slate-400 text-ui-body">
                    No tasks executed
                  </div>
                ) : (
                  flowRun.taskRuns.map(task => (
                    <div
                      key={task.id}
                      className="p-ui-section hover:bg-slate-50/50 transition-colors flex items-center justify-between group"
                    >
                      <div className="flex items-start gap-3">
                        <div
                          className={cn(
                            'mt-1 w-2 h-2 rounded-full',
                            getStateColor(task.state)
                              .split(' ')[0]
                              .replace('/10', '')
                          )}
                        />
                        <div>
                          <div className="font-medium text-ui-body text-slate-900 group-hover:text-blue-600 transition-colors">
                            {task.name}
                          </div>
                          <div className="flex items-center gap-2 mt-1">
                            <Badge
                              variant="outline"
                              className={cn(
                                'text-ui-caption px-1.5 h-4 border-none',
                                getStateColor(task.state)
                              )}
                            >
                              {task.state}
                            </Badge>
                            <span className="text-ui-caption text-slate-400 font-mono">
                              {task.totalRunTime
                                ? `${task.totalRunTime.toFixed(2)}s`
                                : '-'}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="text-right text-ui-label text-slate-400 font-mono">
                        <div>
                          {task.startedAt
                            ? format(new Date(task.startedAt), 'HH:mm:ss.SSS')
                            : '-'}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </ScrollArea>
          </Card>

          {/* Right: Logs & Parameters */}
          <div className="flex flex-col gap-ui-panel min-h-0">
            <Card className="flex-1 flex flex-col border-slate-200/60 shadow-sm overflow-hidden bg-slate-950 text-slate-200">
              <CardHeader className="py-3 px-ui-section border-b border-white/10 bg-white/5 flex flex-row items-center justify-between">
                <CardTitle className="text-ui-label font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                  <Terminal size={14} />
                  Logs
                </CardTitle>
              </CardHeader>
              <ScrollArea className="flex-1">
                <div className="p-ui-section font-mono text-ui-label space-y-1">
                  {flowRun.detailedLogs.length === 0 ? (
                    <div className="text-slate-600 italic">
                      No logs available
                    </div>
                  ) : (
                    flowRun.detailedLogs.map((log, i) => (
                      <div
                        key={i}
                        className="flex gap-3 hover:bg-white/5 px-2 py-0.5 -mx-2 rounded"
                      >
                        <span className="text-slate-500 shrink-0 select-none w-20">
                          {format(new Date(log.time), 'HH:mm:ss')}
                        </span>
                        <span
                          className={cn(
                            'shrink-0 w-16 font-bold',
                            log.level >= 40
                              ? 'text-red-400'
                              : log.level >= 30
                                ? 'text-yellow-400'
                                : 'text-blue-400'
                          )}
                        >
                          {log.level >= 40
                            ? 'ERROR'
                            : log.level >= 30
                              ? 'WARN'
                              : 'INFO'}
                        </span>
                        <span className="break-all whitespace-pre-wrap text-slate-300">
                          {log.message}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </Card>

            <Card className="shrink-0 border-slate-200/60 shadow-sm">
              <CardHeader className="py-3 px-ui-section border-b">
                <CardTitle className="text-ui-label font-bold uppercase tracking-wider text-slate-500">
                  Parameters
                </CardTitle>
              </CardHeader>
              <CardContent className="p-ui-section">
                <pre className="text-ui-label font-mono bg-slate-50 p-3 rounded-lg overflow-x-auto text-slate-600">
                  {formatParameters(flowRun.parameters)}
                </pre>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

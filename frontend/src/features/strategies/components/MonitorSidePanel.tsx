import { Filter, Terminal, List } from 'lucide-react';
import { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type {
  StrategyExecutionLogsQuery as StrategyExecutionLogsQueryData,
  StrategyExecutionLogsQueryVariables,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import type {
  ExecutionTraceView,
  StrategyDecision,
  TradeIntentView,
} from '../domain/types';
import { StrategyExecutionLogsQuery } from '../hooks/strategyInstanceOperations';
import {
  useStrategyLogs,
  type StrategyLogEntry,
} from '../hooks/useStrategyLogs';

interface LogEntry {
  rawTime: string;
  timestamp: string;
  level: StrategyLogEntry['level'];
  source: string;
  message: string;
}

interface IntentEntry {
  id: string;
  time: string;
  rawTime: string;
  symbol: string;
  action: 'BUY' | 'SELL' | 'UNKNOWN';
  price?: number | null;
  quantity?: string | number | null;
  status?: string | null;
  reason?: string | null;
  fillPrice?: number | null;
  fillVolume?: number | null;
}

type IntentStatusFilter =
  | 'ALL'
  | 'FILLED'
  | 'PARTIAL_FILLED'
  | 'ACTIVE'
  | 'REJECTED'
  | 'CLOSED';

const INTENT_ACTION_LABELS = {
  BUY: '买入',
  SELL: '卖出',
  UNKNOWN: '意图',
};

const INTENT_STATUS_LABELS: Record<string, string> = {
  PENDING: '待处理',
  ROUTED: '已下单',
  SUBMITTED: '已下单',
  ACCEPTED: '已受理',
  FILLED: '已成交',
  PARTIAL_FILLED: '部分成交',
  CANCELLED: '已撤单',
  REJECTED: '已拒绝',
  DELAYED: '已延迟',
  EXPIRED: '已过期',
};

const INTENT_STATUS_FILTERS: Array<{
  value: IntentStatusFilter;
  label: string;
}> = [
  { value: 'ALL', label: '全部' },
  { value: 'FILLED', label: '已成交' },
  { value: 'PARTIAL_FILLED', label: '部分成交' },
  { value: 'ACTIVE', label: '处理中' },
  { value: 'REJECTED', label: '已拒绝' },
  { value: 'CLOSED', label: '已撤单/过期' },
];

interface Props {
  strategyId: string;
  runId?: string | null;
  runMode?: string | null;
  backtestId?: string | null;
  backtestVersion?: number | null;
  className?: string;
  isRunning?: boolean;
  decisions?: StrategyDecision[];
  executions?: ExecutionTraceView[];
}

export default function MonitorSidePanel({
  runId,
  runMode,
  backtestId,
  backtestVersion,
  className,
  isRunning = true,
  decisions = [],
  executions = [],
}: Props) {
  const [activeTab, setActiveTab] = useState('logs');
  const [intentStatusFilter, setIntentStatusFilter] =
    useState<IntentStatusFilter>('ALL');
  const logContainerRef = useRef<HTMLDivElement>(null);
  const isBacktest = String(runMode || '').toLowerCase() === 'backtest';
  const resolvedBacktestId = isBacktest ? backtestId || null : null;
  const resolvedBacktestVersion =
    isBacktest && !resolvedBacktestId ? backtestVersion || null : null;

  const [
    { data: fileLogData, fetching: isFileLogFetching, error: fileLogError },
  ] = useQuery<
    StrategyExecutionLogsQueryData,
    StrategyExecutionLogsQueryVariables
  >({
    query: StrategyExecutionLogsQuery,
    variables: {
      runId: runId || '',
      backtestId: resolvedBacktestId,
      version: resolvedBacktestVersion,
      cursor: null,
      limit: 120,
      before: false,
      tail: true,
    },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });

  const { logs: strategyLogs, isConnected } = useStrategyLogs(runId, {
    paused: !runId || !isRunning,
    includeHistory: false,
    maxLogs: 50,
  });

  const fileLogs = useMemo(
    () =>
      (fileLogData?.strategyExecutionLogs.entries || []).map(log =>
        normalizeLogEntry(log)
      ),
    [fileLogData]
  );

  const subscriptionLogs = useMemo(
    () => strategyLogs.map(log => normalizeLogEntry(log)),
    [strategyLogs]
  );

  const logs = useMemo(() => {
    const byKey = new Map<string, LogEntry>();
    [...fileLogs, ...subscriptionLogs].forEach(log => {
      byKey.set(logKey(log), log);
    });
    return [...byKey.values()].sort(
      (a, b) => new Date(a.rawTime).getTime() - new Date(b.rawTime).getTime()
    );
  }, [fileLogs, subscriptionLogs]);

  const intents = useMemo(
    () => buildIntentEntries(decisions, executions, isBacktest),
    [decisions, executions, isBacktest]
  );
  const intentStatusCounts = useMemo(
    () => countIntentStatuses(intents),
    [intents]
  );
  const filteredIntents = useMemo(
    () =>
      intentStatusFilter === 'ALL'
        ? intents
        : intents.filter(
            intent => getIntentStatusGroup(intent.status) === intentStatusFilter
          ),
    [intentStatusFilter, intents]
  );

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div
      className={cn(
        'flex flex-col h-full bg-[#0F1729]/50 border-l border-white/5',
        className
      )}
    >
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex flex-col h-full"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
          <TabsList className="bg-transparent h-7 p-0 gap-2">
            <TabsTrigger
              value="logs"
              className="h-7 px-3 text-[10px] font-bold uppercase tracking-wider data-[state=active]:bg-white/10 data-[state=active]:text-white text-slate-500 hover:text-slate-300 rounded-md transition-all"
            >
              <Terminal size={10} className="mr-1.5" /> 日志
            </TabsTrigger>
            <TabsTrigger
              value="intents"
              className="h-7 px-3 text-[10px] font-bold uppercase tracking-wider data-[state=active]:bg-white/10 data-[state=active]:text-white text-slate-500 hover:text-slate-300 rounded-md transition-all"
            >
              <List size={10} className="mr-1.5" /> 意图
            </TabsTrigger>
          </TabsList>

          <div className="flex gap-1.5">
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                isConnected ? 'bg-emerald-500' : 'bg-slate-700'
              }`}
            />
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                isConnected ? 'bg-emerald-500' : 'bg-slate-700'
              }`}
            />
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                isRunning ? 'bg-emerald-500 animate-pulse' : 'bg-slate-700'
              }`}
            />
          </div>
        </div>

        <div className="flex-1 min-h-0 relative">
          <TabsContent value="logs" className="absolute inset-0 m-0 p-0">
            <div
              ref={logContainerRef}
              className="execution-log-scrollbar h-full overflow-y-auto p-4 space-y-1 font-mono text-[9px]"
            >
              {logs.map((log, i) => {
                const currentYear = getTimestampYear(log.rawTime);
                const previousYear =
                  i > 0 ? getTimestampYear(logs[i - 1].rawTime) : null;
                const shouldShowYear =
                  currentYear && currentYear !== previousYear;

                return (
                  <div key={`${log.rawTime}-${log.message}-${i}`}>
                    {shouldShowYear && (
                      <div className="my-2 flex items-center gap-2 text-[9px] font-bold text-slate-500">
                        <div className="h-px flex-1 bg-white/5" />
                        <span>{currentYear}</span>
                        <div className="h-px flex-1 bg-white/5" />
                      </div>
                    )}
                    <div className="flex gap-2 opacity-80 hover:opacity-100 hover:bg-white/5 px-1 py-0.5 rounded transition-colors">
                      <span className="w-[72px] text-slate-600 shrink-0">
                        {log.timestamp}
                      </span>
                      <span
                        className={cn(
                          'font-bold shrink-0 w-14',
                          log.level === 'INFO' && 'text-blue-400',
                          log.level === 'SUCCESS' && 'text-emerald-400',
                          log.level === 'DEBUG' && 'text-slate-500',
                          log.level === 'WARNING' && 'text-orange-400',
                          log.level === 'ERROR' && 'text-rose-400'
                        )}
                      >
                        {log.level}
                      </span>
                      <span className="w-16 shrink-0 truncate text-slate-500">
                        [{log.source}]
                      </span>
                      <span className="text-slate-300 truncate">
                        {log.message}
                      </span>
                    </div>
                  </div>
                );
              })}
              {logs.length === 0 && !isFileLogFetching && (
                <div className="p-8 text-center">
                  <span className="text-[9px] text-slate-600 uppercase tracking-widest">
                    暂无运行日志
                  </span>
                </div>
              )}
              {fileLogError && (
                <div className="p-4 text-center text-[9px] font-bold text-rose-400">
                  日志文件加载失败
                </div>
              )}
            </div>
          </TabsContent>

          <TabsContent
            value="intents"
            className="execution-log-scrollbar absolute inset-0 m-0 overflow-y-auto p-0"
          >
            <div className="sticky top-0 z-10 border-b border-white/5 bg-[#0F1729]/95 px-3 py-2 backdrop-blur">
              <div className="flex items-center gap-1 overflow-x-auto">
                <Filter size={11} className="shrink-0 text-slate-500" />
                <div className="flex min-w-max items-center rounded-md bg-white/[0.04] p-0.5">
                  {INTENT_STATUS_FILTERS.map(filter => {
                    const isActive = intentStatusFilter === filter.value;
                    return (
                      <button
                        key={filter.value}
                        type="button"
                        aria-pressed={isActive}
                        title={filter.label}
                        onClick={() => setIntentStatusFilter(filter.value)}
                        className={cn(
                          'flex h-6 items-center gap-1 rounded px-2 text-[9px] font-bold transition-colors',
                          isActive
                            ? 'bg-white/10 text-white'
                            : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
                        )}
                      >
                        <span>{filter.label}</span>
                        <span
                          className={cn(
                            'font-mono text-[8px]',
                            isActive ? 'text-slate-200' : 'text-slate-600'
                          )}
                        >
                          {intentStatusCounts[filter.value]}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
            <div className="divide-y divide-white/5">
              {filteredIntents.map(intent => (
                <div
                  key={intent.id}
                  className="p-3 hover:bg-white/5 transition-colors flex items-start justify-between gap-3 group"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <Badge
                        variant="outline"
                        className={cn(
                          'h-4 px-1 text-[8px] font-black border-0 rounded-sm',
                          intent.action === 'BUY'
                            ? 'bg-rose-500/20 text-rose-300'
                            : intent.action === 'SELL'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-slate-500/20 text-slate-300'
                        )}
                      >
                        {INTENT_ACTION_LABELS[intent.action]}
                      </Badge>
                      <span className="text-[10px] font-bold text-slate-200 truncate">
                        {intent.symbol}
                      </span>
                      {intent.status && (
                        <span
                          className={cn(
                            'text-[8px] font-bold rounded-sm px-1.5 py-0.5',
                            getStatusClass(intent.status)
                          )}
                        >
                          {formatIntentStatus(intent.status)}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-[9px] text-slate-600 font-mono">
                      <span>{intent.time}</span>
                      {intent.quantity !== null &&
                        intent.quantity !== undefined && (
                          <span>{formatQuantity(intent.quantity)}股</span>
                        )}
                    </div>
                    {intent.reason && (
                      <div className="mt-1 max-w-[220px] truncate text-[9px] text-slate-500">
                        {intent.reason}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-[10px] font-mono font-bold text-slate-300">
                      {formatMoney(intent.price)}
                    </div>
                    {intent.fillPrice && (
                      <div className="mt-1 text-[9px] font-mono text-amber-300">
                        成交 {formatMoney(intent.fillPrice)}
                        {intent.fillVolume ? ` / ${intent.fillVolume}股` : ''}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {filteredIntents.length === 0 && (
                <div className="p-8 text-center">
                  <span className="text-[9px] text-slate-600 uppercase tracking-widest">
                    暂无匹配意图
                  </span>
                </div>
              )}
              <div className="p-4 text-center">
                <span className="text-[9px] text-slate-600 uppercase tracking-widest">
                  意图列表已结束
                </span>
              </div>
            </div>
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}

function parseTimestamp(timestamp: string) {
  try {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? null : date;
  } catch {
    return null;
  }
}

function formatTimestamp(timestamp: string, includeDate = false): string {
  const date = parseTimestamp(timestamp);
  if (!date) return timestamp;
  const time = date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  if (!includeDate) return time;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}/${month}/${day} ${time}`;
}

function formatLogTimestamp(timestamp: string): string {
  const date = parseTimestamp(timestamp);
  if (!date) return timestamp;
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const time = date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  return `${month}-${day} ${time}`;
}

function getTimestampYear(timestamp: string) {
  const date = parseTimestamp(timestamp);
  return date ? String(date.getFullYear()) : null;
}

function normalizeLogEntry(log: {
  timestamp: string;
  level: unknown;
  source: string;
  message: string;
}): LogEntry {
  return {
    rawTime: log.timestamp,
    timestamp: formatLogTimestamp(log.timestamp),
    level: String(log.level || 'INFO') as StrategyLogEntry['level'],
    source: log.source,
    message: log.message,
  };
}

function logKey(log: LogEntry) {
  return `${log.rawTime}|${log.level}|${log.source}|${log.message}`;
}

function readNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (
    typeof value === 'string' &&
    value.trim() &&
    !Number.isNaN(Number(value))
  ) {
    return Number(value);
  }
  return null;
}

function normalizeAction(value?: string | null): IntentEntry['action'] {
  const side = (value || '').toUpperCase();
  if (side.includes('BUY') || side.includes('买')) return 'BUY';
  if (side.includes('SELL') || side.includes('卖')) return 'SELL';
  return 'UNKNOWN';
}

function formatMoney(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return `¥${value.toFixed(2)}`;
}

function formatQuantity(value: string | number) {
  if (typeof value === 'number') return value.toLocaleString('zh-CN');
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString('zh-CN') : value;
}

function formatIntentStatus(status: string) {
  return INTENT_STATUS_LABELS[status.toUpperCase()] || status;
}

function getStatusClass(status: string) {
  const value = status.toUpperCase();
  if (value.includes('FILL') || value.includes('TRADED')) {
    return 'bg-amber-500/15 text-amber-300';
  }
  if (value.includes('REJECT') || value.includes('CANCEL')) {
    return 'bg-orange-500/15 text-orange-300';
  }
  if (value.includes('ROUT') || value.includes('ACCEPT')) {
    return 'bg-sky-500/15 text-sky-300';
  }
  return 'bg-slate-500/15 text-slate-300';
}

function getIntentStatusGroup(status?: string | null): IntentStatusFilter {
  const value = (status || 'PENDING').toUpperCase();
  if (value.includes('PARTIAL')) return 'PARTIAL_FILLED';
  if (value.includes('FILL') || value.includes('TRADED')) return 'FILLED';
  if (value.includes('REJECT')) return 'REJECTED';
  if (value.includes('CANCEL') || value.includes('EXPIRE')) return 'CLOSED';
  return 'ACTIVE';
}

function countIntentStatuses(
  intents: IntentEntry[]
): Record<IntentStatusFilter, number> {
  const counts: Record<IntentStatusFilter, number> = {
    ALL: intents.length,
    FILLED: 0,
    PARTIAL_FILLED: 0,
    ACTIVE: 0,
    REJECTED: 0,
    CLOSED: 0,
  };
  intents.forEach(intent => {
    counts[getIntentStatusGroup(intent.status)] += 1;
  });
  return counts;
}

function intentStatus(intent: TradeIntentView, execution?: ExecutionTraceView) {
  return (
    execution?.orderStatus ||
    execution?.fillStatus ||
    intent.status ||
    'PENDING'
  );
}

function intentTime(decision: StrategyDecision, intent: TradeIntentView) {
  return intent.createdAt || decision.decidedAt;
}

function buildIntentEntries(
  decisions: StrategyDecision[],
  executions: ExecutionTraceView[],
  includeDate = false
): IntentEntry[] {
  const traces = new Map(
    executions
      .filter(execution => execution.intentId)
      .map(execution => [execution.intentId, execution])
  );
  const includedIntentIds = new Set<string>();
  const entries: IntentEntry[] = [];

  decisions.forEach(decision => {
    decision.tradeIntents.forEach(intent => {
      if (includedIntentIds.has(intent.id)) return;
      const execution = traces.get(intent.id);
      const rawTime = intentTime(decision, intent);
      includedIntentIds.add(intent.id);
      entries.push({
        id: intent.id,
        rawTime,
        time: formatTimestamp(rawTime, includeDate),
        symbol: intent.instrumentCode,
        action: normalizeAction(intent.side),
        price: readNumber(intent.priceIntent),
        quantity: intent.quantityIntent,
        status: intentStatus(intent, execution),
        reason: execution?.reason || intent.reason,
        fillPrice: readNumber(execution?.executedPrice),
        fillVolume: readNumber(execution?.executedVolume),
      });
    });
  });

  executions.forEach(execution => {
    if (includedIntentIds.has(execution.intentId)) return;
    const rawTime =
      execution.executedTime || execution.updatedAt || execution.createdAt;
    if (!rawTime) return;
    entries.push({
      id: execution.id,
      rawTime,
      time: formatTimestamp(rawTime, includeDate),
      symbol: execution.instrumentCode,
      action: normalizeAction(execution.side),
      price: readNumber(execution.executedPrice),
      quantity: execution.executedVolume,
      status: execution.orderStatus || execution.fillStatus,
      reason: execution.reason,
      fillPrice: readNumber(execution.executedPrice),
      fillVolume: readNumber(execution.executedVolume),
    });
  });

  return entries.sort(
    (a, b) => new Date(b.rawTime).getTime() - new Date(a.rawTime).getTime()
  );
}

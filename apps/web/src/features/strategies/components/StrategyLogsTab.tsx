import {
  Terminal,
  Pause,
  Play,
  Copy,
  Download,
  FileText,
  Trash2,
  Wifi,
  WifiOff,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { UIEvent } from 'react';
import { useClient, useQuery } from 'urql';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useGraphqlWsStatus } from '@/core/graphql/ws-status';
import type {
  StrategyExecutionLogsQuery as StrategyExecutionLogsQueryData,
  StrategyExecutionLogsQueryVariables,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { StrategyExecutionLogsQuery } from '../hooks/strategyInstanceOperations';
import {
  useStrategyLogs,
  type StrategyLogEntry,
} from '../hooks/useStrategyLogs';

const PAGE_SIZE = 300;
const MAX_RETAINED_LOGS = 1800;
const ROW_HEIGHT = 24;
const OVERSCAN_ROWS = 12;
const MIN_LOG_VIEWPORT_HEIGHT = 120;
const LOG_VIEWPORT_BOTTOM_GAP = 12;

interface StrategyLogsTabProps {
  /** 策略运行实例ID (用于订阅日志) */
  runId: string | null | undefined;
  /** 策略名称 (用于显示) */
  strategyName: string;
  /** 是否正在运行 */
  isRunning: boolean;
  /** 运行模式 */
  runMode?: string | null;
  /** 回测版本ID */
  backtestId?: string | null;
  /** 回测版本号 */
  backtestVersion?: number | null;
  /** 是否填满父容器，避免页面和终端出现双滚动条 */
  fillAvailable?: boolean;
  /** 运行实例状态 (可选,用于更精细的控制) */
  status?:
    | 'RUNNING'
    | 'STARTING'
    | 'PAUSED'
    | 'STOPPED'
    | 'PENDING'
    | 'COMPLETED'
    | 'ERROR';
}

type LoadedStrategyLogPage = {
  entries: StrategyLogEntry[];
  startCursor: number;
  endCursor: number;
  hasPreviousPage: boolean;
  hasNextPage: boolean;
  totalLines: number;
  fileSizeBytes: number;
  sourcePath?: string | null;
};

function logKey(log: StrategyLogEntry) {
  return `${log.runId}|${log.timestamp}|${log.level}|${log.source}|${log.message}`;
}

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

type StrategyExecutionLogRecord =
  StrategyExecutionLogsQueryData['strategyExecutionLogs']['entries'][number];

function normalizeLog(raw: StrategyExecutionLogRecord): StrategyLogEntry {
  return {
    runId: raw.runId,
    timestamp: raw.timestamp,
    level: String(raw.level || 'INFO') as StrategyLogEntry['level'],
    message: raw.message,
    source: raw.source,
  };
}

function normalizePage(
  raw:
    StrategyExecutionLogsQueryData['strategyExecutionLogs'] | null | undefined
): LoadedStrategyLogPage | null {
  if (!raw) return null;
  return {
    entries: raw.entries.map(normalizeLog),
    startCursor: raw.startCursor,
    endCursor: raw.endCursor,
    hasPreviousPage: raw.hasPreviousPage,
    hasNextPage: raw.hasNextPage,
    totalLines: raw.totalLines,
    fileSizeBytes: raw.fileSizeBytes,
    sourcePath: raw.sourcePath || null,
  };
}

function trimLogWindow(logs: StrategyLogEntry[], keepTail: boolean) {
  if (logs.length <= MAX_RETAINED_LOGS) return logs;
  return keepTail
    ? logs.slice(-MAX_RETAINED_LOGS)
    : logs.slice(0, MAX_RETAINED_LOGS);
}

function appendUniqueLogs(
  currentLogs: StrategyLogEntry[],
  nextLogs: StrategyLogEntry[]
) {
  if (nextLogs.length === 0) return currentLogs;

  const existing = new Set(currentLogs.map(logKey));
  const uniqueNextLogs = nextLogs.filter(item => !existing.has(logKey(item)));
  if (uniqueNextLogs.length === 0) return currentLogs;

  return trimLogWindow([...currentLogs, ...uniqueNextLogs], true);
}

function formatBytes(value?: number) {
  const size = Number(value || 0);
  if (size <= 0) return '0 KB';
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 策略日志终端组件
 *
 * 使用文件分页作为历史真源，WebSocket 只负责实时增量。
 */
export default function StrategyLogsTab({
  runId,
  strategyName,
  isRunning,
  runMode,
  backtestId,
  backtestVersion,
  fillAvailable = false,
  status,
}: StrategyLogsTabProps) {
  const client = useClient();
  const [isPaused, setIsPaused] = useState(false);
  const [logs, setLogs] = useState<StrategyLogEntry[]>([]);
  const [pageInfo, setPageInfo] = useState<LoadedStrategyLogPage | null>(null);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [recoveryError, setRecoveryError] = useState<Error | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(350);
  const [autoFollow, setAutoFollow] = useState(true);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const autoFollowRef = useRef(true);
  const pageInfoRef = useRef<LoadedStrategyLogPage | null>(null);
  const lastLiveLogKeyRef = useRef<string | null>(null);
  const previousWsStatusRef = useRef<string>('idle');
  const pendingScrollAdjustRef = useRef(0);
  const { closeMenu, menu, openAtPointer } = useStudioMenu<StrategyLogEntry>();
  const wsStatus = useGraphqlWsStatus();

  const updateLogViewportHeight = useCallback(() => {
    const container = logContainerRef.current;
    if (!container || typeof window === 'undefined') return;

    if (fillAvailable) {
      const nextHeight = Math.max(
        MIN_LOG_VIEWPORT_HEIGHT,
        Math.floor(container.clientHeight || 0)
      );
      setViewportHeight(prev =>
        Math.abs(prev - nextHeight) > 1 ? nextHeight : prev
      );
      return;
    }

    const { top } = container.getBoundingClientRect();
    const nextHeight = Math.max(
      MIN_LOG_VIEWPORT_HEIGHT,
      Math.floor(window.innerHeight - top - LOG_VIEWPORT_BOTTOM_GAP)
    );
    setViewportHeight(prev =>
      Math.abs(prev - nextHeight) > 1 ? nextHeight : prev
    );
  }, [fillAvailable]);

  const isBacktest = String(runMode || '').toLowerCase() === 'backtest';
  const resolvedBacktestId = isBacktest ? backtestId || null : null;
  const resolvedBacktestVersion =
    isBacktest && !resolvedBacktestId ? backtestVersion || null : null;

  const [{ data: fileData, fetching: isFileFetching, error: fileError }] =
    useQuery<
      StrategyExecutionLogsQueryData,
      StrategyExecutionLogsQueryVariables
    >({
      query: StrategyExecutionLogsQuery,
      variables: {
        runId: runId || '',
        backtestId: resolvedBacktestId,
        version: resolvedBacktestVersion,
        cursor: null,
        limit: PAGE_SIZE,
        before: false,
        tail: true,
      },
      pause: !runId,
      requestPolicy: 'cache-and-network',
    });

  const shouldSubscribe =
    !!runId &&
    (isRunning ||
      status === 'RUNNING' ||
      status === 'STARTING' ||
      status === 'PAUSED' ||
      status === 'PENDING');

  const {
    logs: liveLogs,
    isConnected,
    error,
    clearLogs,
  } = useStrategyLogs(runId, {
    paused: isPaused || !shouldSubscribe,
    includeHistory: false,
    maxLogs: 100,
  });

  useEffect(() => {
    setLogs([]);
    setPageInfo(null);
    pageInfoRef.current = null;
    setRecoveryError(null);
    setScrollTop(0);
    setAutoFollow(true);
    autoFollowRef.current = true;
    lastLiveLogKeyRef.current = null;
    previousWsStatusRef.current = 'idle';
    pendingScrollAdjustRef.current = 0;
  }, [runId, resolvedBacktestId, resolvedBacktestVersion]);

  useEffect(() => {
    const page = normalizePage(fileData?.strategyExecutionLogs);
    if (!page) return;
    const isInitialPage = !pageInfoRef.current;

    setLogs(prev =>
      prev.length === 0 ? page.entries : appendUniqueLogs(prev, page.entries)
    );
    setPageInfo(prev => {
      const nextPageInfo = prev
        ? {
            ...prev,
            endCursor: Math.max(prev.endCursor, page.endCursor),
            hasNextPage: page.hasNextPage,
            totalLines: Math.max(prev.totalLines, page.totalLines),
            fileSizeBytes: page.fileSizeBytes,
            sourcePath: page.sourcePath,
          }
        : page;
      pageInfoRef.current = nextPageInfo;
      return nextPageInfo;
    });

    if (isInitialPage) {
      autoFollowRef.current = true;
      setAutoFollow(true);
    }
  }, [fileData]);

  useEffect(() => {
    const liveLog = liveLogs[liveLogs.length - 1];
    if (!liveLog) return;
    const key = logKey(liveLog);
    if (lastLiveLogKeyRef.current === key) return;
    lastLiveLogKeyRef.current = key;

    setLogs(prev => {
      if (prev.some(item => logKey(item) === key)) return prev;
      return trimLogWindow([...prev, liveLog], true);
    });
  }, [liveLogs]);

  const recoverMissingLogs = useCallback(async () => {
    if (!runId || !pageInfoRef.current) return;

    try {
      const currentPageInfo = pageInfoRef.current;
      const result = await client
        .query<
          StrategyExecutionLogsQueryData,
          StrategyExecutionLogsQueryVariables
        >(
          StrategyExecutionLogsQuery,
          {
            runId,
            backtestId: resolvedBacktestId,
            version: resolvedBacktestVersion,
            cursor: currentPageInfo.endCursor,
            limit: PAGE_SIZE,
            before: false,
            tail: false,
          },
          { requestPolicy: 'network-only' }
        )
        .toPromise();

      if (result.error) {
        setRecoveryError(result.error);
        return;
      }

      const page = normalizePage(result.data?.strategyExecutionLogs);
      if (!page) return;

      setRecoveryError(null);
      setLogs(prev => appendUniqueLogs(prev, page.entries));
      setPageInfo(prev => {
        const nextPageInfo = prev
          ? {
              ...prev,
              endCursor: Math.max(prev.endCursor, page.endCursor),
              hasNextPage: page.hasNextPage,
              totalLines: Math.max(prev.totalLines, page.totalLines),
              fileSizeBytes: page.fileSizeBytes,
              sourcePath: page.sourcePath,
            }
          : page;
        pageInfoRef.current = nextPageInfo;
        return nextPageInfo;
      });
    } catch (caughtError) {
      setRecoveryError(
        caughtError instanceof Error
          ? caughtError
          : new Error('策略日志恢复失败')
      );
    }
  }, [client, resolvedBacktestId, resolvedBacktestVersion, runId]);

  useEffect(() => {
    const wasConnected = previousWsStatusRef.current === 'connected';
    previousWsStatusRef.current = wsStatus;

    if (
      wsStatus === 'connected' &&
      !wasConnected &&
      shouldSubscribe &&
      !isPaused
    ) {
      void recoverMissingLogs();
    }
  }, [isPaused, recoverMissingLogs, shouldSubscribe, wsStatus]);

  useEffect(() => {
    if (!shouldSubscribe || isPaused || wsStatus !== 'connected') return;

    const recoverOnVisible = () => {
      if (document.visibilityState === 'visible') {
        void recoverMissingLogs();
      }
    };

    const recoverOnFocus = () => {
      void recoverMissingLogs();
    };

    document.addEventListener('visibilitychange', recoverOnVisible);
    window.addEventListener('focus', recoverOnFocus);
    return () => {
      document.removeEventListener('visibilitychange', recoverOnVisible);
      window.removeEventListener('focus', recoverOnFocus);
    };
  }, [isPaused, recoverMissingLogs, shouldSubscribe, wsStatus]);

  useLayoutEffect(() => {
    const container = logContainerRef.current;
    if (!container) return;

    if (pendingScrollAdjustRef.current) {
      container.scrollTop += pendingScrollAdjustRef.current;
      pendingScrollAdjustRef.current = 0;
      return;
    }

    if (autoFollowRef.current && !isPaused) {
      container.scrollTop = container.scrollHeight;
    }
  }, [logs.length, isPaused]);

  useLayoutEffect(() => {
    updateLogViewportHeight();

    const handleViewportChange = () => {
      window.requestAnimationFrame(updateLogViewportHeight);
    };

    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);

    const observer =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(handleViewportChange)
        : null;
    if (observer) {
      observer.observe(document.body);
      if (logContainerRef.current?.parentElement) {
        observer.observe(logContainerRef.current.parentElement);
      }
    }

    return () => {
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
      observer?.disconnect();
    };
  }, [updateLogViewportHeight]);

  const loadOlderLogs = useCallback(async () => {
    if (!runId || !pageInfo?.hasPreviousPage || isLoadingOlder) return;
    setIsLoadingOlder(true);
    try {
      const result = await client
        .query<
          StrategyExecutionLogsQueryData,
          StrategyExecutionLogsQueryVariables
        >(
          StrategyExecutionLogsQuery,
          {
            runId,
            backtestId: resolvedBacktestId,
            version: resolvedBacktestVersion,
            cursor: pageInfo.startCursor,
            limit: PAGE_SIZE,
            before: true,
            tail: false,
          },
          { requestPolicy: 'network-only' }
        )
        .toPromise();
      const page = normalizePage(result.data?.strategyExecutionLogs);
      if (!page || page.entries.length === 0) return;

      pendingScrollAdjustRef.current = page.entries.length * ROW_HEIGHT;
      setLogs(prev => {
        const existing = new Set(prev.map(logKey));
        const older = page.entries.filter(item => !existing.has(logKey(item)));
        return trimLogWindow([...older, ...prev], false);
      });
      setPageInfo(prev =>
        prev
          ? {
              ...prev,
              startCursor: page.startCursor,
              hasPreviousPage: page.hasPreviousPage,
              totalLines: page.totalLines,
              fileSizeBytes: page.fileSizeBytes,
              sourcePath: page.sourcePath,
            }
          : page
      );
      pageInfoRef.current = pageInfoRef.current
        ? {
            ...pageInfoRef.current,
            startCursor: page.startCursor,
            hasPreviousPage: page.hasPreviousPage,
            totalLines: page.totalLines,
            fileSizeBytes: page.fileSizeBytes,
            sourcePath: page.sourcePath,
          }
        : page;
    } finally {
      setIsLoadingOlder(false);
    }
  }, [
    client,
    isLoadingOlder,
    pageInfo,
    resolvedBacktestId,
    resolvedBacktestVersion,
    runId,
  ]);

  const handleScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const container = event.currentTarget;
      const nextAutoFollow =
        container.scrollHeight - container.scrollTop - container.clientHeight <
        48;
      autoFollowRef.current = nextAutoFollow;
      setAutoFollow(nextAutoFollow);
      setScrollTop(container.scrollTop);
      setViewportHeight(container.clientHeight || 350);

      if (container.scrollTop < 64) {
        void loadOlderLogs();
      }
    },
    [loadOlderLogs]
  );

  const visibleRange = useMemo(() => {
    const start = Math.max(
      0,
      Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN_ROWS
    );
    const end = Math.min(
      logs.length,
      Math.ceil((scrollTop + viewportHeight) / ROW_HEIGHT) + OVERSCAN_ROWS
    );
    return { start, end };
  }, [logs.length, scrollTop, viewportHeight]);

  const visibleLogs = useMemo(
    () => logs.slice(visibleRange.start, visibleRange.end),
    [logs, visibleRange.end, visibleRange.start]
  );

  const getLevelColor = (level: StrategyLogEntry['level']) => {
    switch (level) {
      case 'DEBUG':
        return 'text-slate-400';
      case 'INFO':
        return 'text-blue-400';
      case 'SUCCESS':
        return 'text-emerald-400';
      case 'WARNING':
        return 'text-amber-400';
      case 'ERROR':
        return 'text-rose-400';
      default:
        return 'text-slate-400';
    }
  };

  const formatTimestamp = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString('zh-CN', { hour12: false });
    } catch {
      return timestamp;
    }
  };

  const loadedSummary = pageInfo
    ? `${logs.length}/${Math.max(logs.length, pageInfo.totalLines)} 行 · ${formatBytes(pageInfo.fileSizeBytes)}`
    : isFileFetching
      ? '加载中'
      : '未找到日志文件';

  const buildLoadedText = () =>
    logs
      .map(
        l =>
          `${formatTimestamp(l.timestamp)} [${l.level}] [${l.source}] ${l.message}`
      )
      .join('\n');

  const handleCopy = () => {
    navigator.clipboard.writeText(buildLoadedText());
  };

  const handleDownload = () => {
    const blob = new Blob([buildLoadedText()], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `strategy-${runId}-logs-loaded.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleClear = () => {
    clearLogs();
    setLogs([]);
  };

  const realtimeStatus = isConnected
    ? {
        icon: <Wifi size={12} className="text-emerald-400" />,
        text: '已连接',
        className: 'text-slate-500',
      }
    : wsStatus === 'connecting'
      ? {
          icon: <Wifi size={12} className="text-blue-500" />,
          text: '连接中',
          className: 'text-blue-500',
        }
      : wsStatus === 'reconnecting' ||
          (wsStatus === 'closed' && shouldSubscribe)
        ? {
            icon: <Wifi size={12} className="text-amber-400" />,
            text: '重连中',
            className: 'text-amber-400',
          }
        : error || recoveryError || wsStatus === 'error'
          ? {
              icon: <WifiOff size={12} className="text-rose-400" />,
              text: '连接错误',
              className: 'text-rose-400',
            }
          : {
              icon: <WifiOff size={12} className="text-slate-500" />,
              text: '未连接',
              className: 'text-slate-500',
            };

  return (
    <>
      <Card
        className={cn(
          'overflow-hidden rounded-lg border border-white/5 bg-slate-950 shadow-2xl',
          fillAvailable && 'flex h-full min-h-0 flex-col'
        )}
      >
        <div className="flex items-center justify-between border-b border-white/5 bg-slate-900/40 px-6 py-4 backdrop-blur-md">
          <div className="flex min-w-0 items-center gap-3">
            <Terminal size={14} className="shrink-0 text-slate-400" />
            <span className="truncate font-mono text-[11px] font-bold text-slate-300">
              终端 - {strategyName}
            </span>
            <Badge
              variant="outline"
              className={cn(
                'rounded-full px-2 py-0.5 text-[8px] font-black uppercase tracking-widest',
                isRunning
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                  : 'border-slate-600 text-slate-500'
              )}
            >
              {isRunning ? '运行中' : '已停止'}
            </Badge>
            <div className="flex shrink-0 items-center gap-1.5 text-[9px] font-bold">
              {realtimeStatus.icon}
              <span className={realtimeStatus.className}>
                {realtimeStatus.text}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white"
              onClick={() => setIsPaused(!isPaused)}
            >
              {isPaused ? <Play size={14} /> : <Pause size={14} />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white"
              onClick={handleCopy}
            >
              <Copy size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white"
              onClick={handleDownload}
            >
              <Download size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-rose-400"
              onClick={handleClear}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        </div>

        <div className="border-b border-slate-800 bg-slate-900/30 px-6 py-2">
          <div className="flex items-center justify-center gap-3 text-center">
            <span className="rounded border border-blue-600/30 bg-blue-600/5 px-3 py-0.5 font-mono text-[10px] text-blue-500">
              A股量化交易策略日志终端
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              {loadedSummary}
            </span>
            {fileError && (
              <span className="font-mono text-[10px] text-rose-400">
                文件加载错误
              </span>
            )}
          </div>
        </div>

        <div
          ref={logContainerRef}
          className={cn(
            'execution-log-scrollbar overflow-auto p-5 font-mono text-[10px] leading-relaxed',
            fillAvailable && 'min-h-0 flex-1'
          )}
          style={fillAvailable ? undefined : { height: `${viewportHeight}px` }}
          onScroll={handleScroll}
        >
          {!runId ? (
            <div className="py-12 text-center text-slate-600">
              请选择一个运行中的策略实例
            </div>
          ) : logs.length === 0 && !isFileFetching ? (
            <div className="py-12 text-center text-slate-600">暂无日志记录</div>
          ) : (
            <div
              className="relative min-w-max"
              style={{ height: `${logs.length * ROW_HEIGHT}px` }}
            >
              {isLoadingOlder && (
                <div className="absolute left-2 top-0 z-10 text-slate-500">
                  正在加载更早日志...
                </div>
              )}
              {visibleLogs.map((log, index) => {
                const absoluteIndex = visibleRange.start + index;
                return (
                  <div
                    key={`${logKey(log)}-${absoluteIndex}`}
                    className="absolute left-0 right-0 flex h-6 items-center gap-3 rounded px-2 hover:bg-slate-900/50"
                    style={{ top: `${absoluteIndex * ROW_HEIGHT}px` }}
                    onContextMenu={event => openAtPointer(event, log)}
                  >
                    <span className="shrink-0 text-slate-600">
                      {formatTimestamp(log.timestamp)}
                    </span>
                    <span
                      className={cn(
                        'shrink-0 font-bold',
                        getLevelColor(log.level)
                      )}
                    >
                      [{log.level.padEnd(7)}]
                    </span>
                    <span className="shrink-0 text-slate-400">
                      [{log.source}]
                    </span>
                    <span className="whitespace-pre text-slate-300">
                      {log.message}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {isRunning && !isPaused && isConnected && autoFollow && (
            <div className="mt-4 flex items-center gap-2 text-slate-500">
              <span className="text-blue-400">[INFO]</span>
              <span>正在等待实时日志...</span>
              <span className="animate-pulse">|</span>
            </div>
          )}
        </div>
      </Card>

      <StudioMenu
        ariaLabel="策略日志菜单"
        menu={menu}
        onClose={closeMenu}
        width={220}
        items={[
          {
            id: 'copy-line',
            label: '复制整行日志',
            icon: <Copy size={14} />,
            onSelect: () =>
              copyText(
                menu?.payload
                  ? `${formatTimestamp(menu.payload.timestamp)} [${menu.payload.level}] [${menu.payload.source}] ${menu.payload.message}`
                  : ''
              ),
          },
          {
            id: 'copy-message',
            label: '复制消息',
            icon: <FileText size={14} />,
            onSelect: () => copyText(menu?.payload?.message),
          },
          {
            id: 'copy-source',
            label: '复制来源',
            icon: <Copy size={14} />,
            onSelect: () => copyText(menu?.payload?.source),
          },
          {
            id: 'copy-time',
            label: '复制时间',
            icon: <Copy size={14} />,
            onSelect: () => copyText(menu?.payload?.timestamp),
          },
          { id: 'sep-terminal', type: 'separator' },
          {
            id: 'toggle-pause',
            label: isPaused ? '继续跟随日志' : '暂停跟随日志',
            icon: isPaused ? <Play size={14} /> : <Pause size={14} />,
            onSelect: () => setIsPaused(prev => !prev),
          },
        ]}
      />
    </>
  );
}

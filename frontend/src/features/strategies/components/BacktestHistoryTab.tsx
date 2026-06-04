/**
 * 回测版本标签页
 *
 * 显示策略运行的所有回测版本，支持：
 * - 版本列表展示（时间、状态、指标）
 * - 切换到指定版本查看详细数据
 * - 当后端schema更新后，取消 queries.gql 的注释即可使用GraphQL查询
 */

import {
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  Activity,
  Copy,
  Eye,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { useState, type MouseEvent } from 'react';
import { useMutation, useQuery } from 'urql';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { cn } from '@/utils/cn';

import {
  BacktestHistoryQuery,
  DeleteBacktestVersionMutation,
} from '../hooks/strategyInstanceOperations';

// 回测版本记录类型（临时定义，后续从 generated/gql 导入）
interface StrategyBacktest {
  id: string;
  strategyRunId: string;
  version: number;
  parameters?: Record<string, unknown>;
  instruments?: string[];
  backtestStartTime?: string;
  backtestEndTime?: string;
  startTime?: string;
  endTime?: string;
  metrics?: Record<string, number | string | null | undefined>;
  status: string;
  errorMessage?: string;
  resultPath?: string;
  createdAt?: string;
}

interface BacktestHistoryTabProps {
  runId: string;
  mode?: string;
  currentBacktestId?: string | null;
  onTemplateSelect?: () => void;
  onVersionSelect?: (backtest: StrategyBacktest) => void;
  onVersionDeleted?: (backtestId: string) => void;
}

type BacktestMenuPayload =
  | { kind: 'template' }
  | { backtest: StrategyBacktest; kind: 'backtest' };

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

export default function BacktestHistoryTab({
  runId,
  mode,
  currentBacktestId,
  onTemplateSelect,
  onVersionSelect,
  onVersionDeleted,
}: BacktestHistoryTabProps) {
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StrategyBacktest | null>(
    null
  );
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<BacktestMenuPayload>();
  const [{ data, fetching, error }, reexecuteBacktestHistory] = useQuery({
    query: BacktestHistoryQuery,
    variables: { runId },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });
  const [, deleteBacktestVersion] = useMutation(DeleteBacktestVersionMutation);

  const backtests: StrategyBacktest[] = (
    ((data as any)?.backtestHistory || []) as unknown[]
  ).map(raw => {
    const record = (raw || {}) as Record<string, any>;
    return {
      id: String(record.id || ''),
      strategyRunId: String(
        record.strategyRunId || record.strategy_run_id || ''
      ),
      version: Number(record.version || 0),
      parameters: record.parameters,
      instruments: record.instruments,
      backtestStartTime:
        record.backtestStartTime || record.backtest_start_time || undefined,
      backtestEndTime:
        record.backtestEndTime || record.backtest_end_time || undefined,
      startTime: record.startTime || record.start_time || undefined,
      endTime: record.endTime || record.end_time || undefined,
      metrics: record.metrics,
      status: String(record.status || 'PENDING'),
      errorMessage: record.errorMessage || record.error_message || undefined,
      resultPath: record.resultPath || record.result_path || undefined,
      createdAt: record.createdAt || record.created_at || undefined,
    };
  });
  const activeBacktestId = currentBacktestId || selectedVersion;
  const activeBacktest = activeBacktestId
    ? backtests.find(backtest => backtest.id === activeBacktestId)
    : null;

  const getStatusIcon = (status: string) => {
    switch (status.toUpperCase()) {
      case 'COMPLETED':
        return <CheckCircle className="w-4 h-4 text-green-400" />;
      case 'RUNNING':
        return <Activity className="w-4 h-4 text-blue-400 animate-pulse" />;
      case 'ERROR':
        return <XCircle className="w-4 h-4 text-red-400" />;
      case 'PENDING':
        return <Clock className="w-4 h-4 text-yellow-400" />;
      default:
        return <AlertCircle className="w-4 h-4 text-gray-400" />;
    }
  };

  const getStatusBadge = (status: string) => {
    const statusMap: Record<string, { label: string; className: string }> = {
      COMPLETED: {
        label: '已完成',
        className: 'bg-green-500/20 text-green-400 border-green-500/30',
      },
      RUNNING: {
        label: '运行中',
        className: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
      },
      ERROR: {
        label: '错误',
        className: 'bg-red-500/20 text-red-400 border-red-500/30',
      },
      PENDING: {
        label: '等待中',
        className: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
      },
    };
    const config = statusMap[status.toUpperCase()] || {
      label: status,
      className: 'bg-gray-500/20 text-gray-400',
    };
    return (
      <Badge variant="outline" className={cn('text-xs', config.className)}>
        {config.label}
      </Badge>
    );
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '-';
    try {
      return new Date(dateStr).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return dateStr;
    }
  };

  const formatMetric = (value?: number | string | null) => {
    if (value === undefined || value === null) return '-';
    const numeric = Number(value);
    if (Number.isNaN(numeric)) return String(value);
    if (Math.abs(numeric) >= 1) return numeric.toFixed(2);
    return (numeric * 100).toFixed(2) + '%';
  };

  const handleSelectVersion = (backtest: StrategyBacktest) => {
    setSelectedVersion(backtest.id);
    onVersionSelect?.(backtest);
  };

  const handleDeleteVersion = (
    event: MouseEvent<HTMLButtonElement>,
    backtest: StrategyBacktest
  ) => {
    event.stopPropagation();
    if (backtest.status.toUpperCase() === 'RUNNING') return;
    setDeleteError(null);
    setDeleteTarget(backtest);
  };

  const confirmDeleteVersion = async () => {
    if (!deleteTarget) return;
    const backtest = deleteTarget;
    setDeletingId(backtest.id);
    setDeleteError(null);
    try {
      const result = await deleteBacktestVersion({
        runId,
        backtestId: backtest.id,
      });
      if (result.error) {
        throw new Error(result.error.message);
      }
      const payload = (result.data as any)?.deleteBacktestVersion;
      if (!payload?.success) {
        throw new Error(payload?.message || '删除回测版本失败');
      }
      if (activeBacktestId === backtest.id) {
        setSelectedVersion(null);
      }
      onVersionDeleted?.(backtest.id);
      reexecuteBacktestHistory({ requestPolicy: 'network-only' });
      setDeleteTarget(null);
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : '删除回测版本失败'
      );
    } finally {
      setDeletingId(null);
    }
  };

  // 仅在回测模式下显示
  if (mode && mode.toUpperCase() !== 'BACKTEST') {
    return (
      <Card className="bg-gray-900/50 border-gray-800">
        <CardContent className="p-6 text-center text-gray-500">
          仅回测模式支持查看回测版本
        </CardContent>
      </Card>
    );
  }

  if (fetching && backtests.length === 0) {
    return (
      <Card className="bg-gray-900/50 border-gray-800">
        <CardContent className="p-6 text-center text-gray-500">
          <Activity className="w-6 h-6 mx-auto mb-2 animate-spin" />
          加载回测版本...
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="bg-gray-900/50 border-gray-800">
        <CardContent className="p-6 text-center text-red-400">
          加载失败: {error.message}
        </CardContent>
      </Card>
    );
  }

  if (backtests.length === 0) {
    return (
      <Card className="bg-gray-900/50 border-gray-800">
        <CardContent className="p-6 text-center text-gray-500">
          <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>暂无回测版本记录</p>
          <p className="text-sm mt-1 text-gray-600">
            运行回测后，版本记录将显示在这里
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card className="bg-gray-900/50 border-gray-800">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium text-gray-200 flex items-center gap-2">
            <Clock className="w-4 h-4" />
            回测版本
            {!activeBacktestId && (
              <Badge
                variant="outline"
                className="border-emerald-500/30 bg-emerald-500/10 text-xs text-emerald-300"
              >
                当前查看模板
              </Badge>
            )}
            {activeBacktest && (
              <Badge
                variant="outline"
                className="border-blue-500/30 bg-blue-500/10 text-xs text-blue-300"
              >
                当前查看 v{activeBacktest.version}
              </Badge>
            )}
            <Badge variant="secondary" className="ml-auto text-xs">
              共 {backtests.length} 个版本
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {deleteError && !deleteTarget && (
            <div className="mx-4 mb-3 rounded-md border border-red-500/30 bg-red-950/30 px-3 py-2 text-sm text-red-300">
              {deleteError}
            </div>
          )}
          <div className="divide-y divide-gray-800">
            <div
              className={cn(
                'p-4 cursor-pointer transition-colors hover:bg-gray-800/50',
                !activeBacktestId &&
                  'bg-emerald-900/20 border-l-2 border-emerald-500'
              )}
              onContextMenu={event =>
                openAtPointer(event, { kind: 'template' })
              }
              onClick={() => {
                setSelectedVersion(null);
                onTemplateSelect?.();
              }}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex flex-wrap items-center gap-2">
                  <CheckCircle className="w-4 h-4 text-emerald-400" />
                  <span className="font-medium text-gray-200">模板版本</span>
                  <Badge
                    variant="outline"
                    className="bg-emerald-500/20 text-emerald-400 border-emerald-500/30 text-xs"
                  >
                    可编辑
                  </Badge>
                  {!activeBacktestId && (
                    <Badge
                      variant="outline"
                      className="border-emerald-500/30 bg-emerald-500/10 text-xs text-emerald-300"
                    >
                      当前查看
                    </Badge>
                  )}
                </div>
                <span className="text-xs text-gray-500">重新回测来源</span>
              </div>
              <div className="text-sm text-gray-400">
                参数配置和网格簿编辑会更新模板；已完成回测版本保持只读快照。
              </div>
            </div>
            {backtests.map(backtest => {
              const isCurrent = activeBacktestId === backtest.id;
              const isDeleting = deletingId === backtest.id;
              return (
                <div
                  key={backtest.id}
                  className={cn(
                    'p-4 cursor-pointer transition-colors hover:bg-gray-800/50',
                    isCurrent && 'bg-blue-900/20 border-l-2 border-blue-500'
                  )}
                  onContextMenu={event =>
                    openAtPointer(event, { kind: 'backtest', backtest })
                  }
                  onClick={() => handleSelectVersion(backtest)}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {getStatusIcon(backtest.status)}
                      <span className="font-medium text-gray-200">
                        版本 v{backtest.version}
                      </span>
                      {getStatusBadge(backtest.status)}
                      {isCurrent && (
                        <Badge
                          variant="outline"
                          className="border-blue-500/30 bg-blue-500/10 text-xs text-blue-300"
                        >
                          当前查看
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">
                        {formatDate(backtest.createdAt)}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 rounded-lg text-gray-500 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-60"
                        title={
                          backtest.status.toUpperCase() === 'RUNNING'
                            ? '运行中的回测版本不可删除'
                            : `删除回测版本 v${backtest.version}`
                        }
                        disabled={
                          isDeleting ||
                          backtest.status.toUpperCase() === 'RUNNING'
                        }
                        onClick={event => handleDeleteVersion(event, backtest)}
                      >
                        {isDeleting ? (
                          <Activity className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>

                  {/* 回测时间范围 */}
                  <div className="text-sm text-gray-400 mb-2">
                    {backtest.backtestStartTime && backtest.backtestEndTime ? (
                      <span>
                        {formatDate(backtest.backtestStartTime)} ~{' '}
                        {formatDate(backtest.backtestEndTime)}
                      </span>
                    ) : (
                      <span>时间范围未指定</span>
                    )}
                  </div>

                  {/* 关键指标 */}
                  {backtest.metrics &&
                    Object.keys(backtest.metrics).length > 0 &&
                    (() => {
                      const totalPnl = backtest.metrics?.total_pnl;
                      const totalPnlNumber = Number(totalPnl);
                      return (
                        <div className="flex gap-4 text-xs">
                          {totalPnl !== undefined && (
                            <span
                              className={cn(
                                'font-medium',
                                !Number.isNaN(totalPnlNumber) &&
                                  totalPnlNumber >= 0
                                  ? 'text-green-400'
                                  : 'text-red-400'
                              )}
                            >
                              收益: {formatMetric(totalPnl)}
                            </span>
                          )}
                          {backtest.metrics?.win_rate !== undefined && (
                            <span className="text-gray-400">
                              胜率: {formatMetric(backtest.metrics.win_rate)}
                            </span>
                          )}
                          {backtest.metrics?.trades_executed !== undefined && (
                            <span className="text-gray-400">
                              交易次数: {backtest.metrics.trades_executed}
                            </span>
                          )}
                        </div>
                      );
                    })()}

                  {/* 错误信息 */}
                  {backtest.status === 'ERROR' && backtest.errorMessage && (
                    <div className="mt-2 text-xs text-red-400 bg-red-900/20 p-2 rounded">
                      {backtest.errorMessage}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={open => {
          if (open) return;
          setDeleteTarget(null);
          setDeleteError(null);
        }}
        title="确认删除"
        description={
          <div className="space-y-3">
            <p>
              {`确定要删除回测版本 v${deleteTarget?.version ?? ''}？此操作会删除该版本的执行明细、绩效快照和网格簿快照。`}
            </p>
            {deleteError && (
              <p className="rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-2 text-xs font-medium text-red-300">
                {deleteError}
              </p>
            )}
          </div>
        }
        confirmText="删除"
        loadingText="删除中..."
        cancelText="取消"
        variant="destructive"
        loading={deletingId === deleteTarget?.id}
        onConfirm={confirmDeleteVersion}
      />

      <StudioMenu
        ariaLabel="回测版本菜单"
        menu={menu}
        onClose={closeMenu}
        width={216}
        items={[
          {
            id: 'open',
            label:
              menu?.payload?.kind === 'template'
                ? '查看模板版本'
                : '查看回测版本',
            icon: <Eye size={14} />,
            onSelect: () => {
              if (menu?.payload?.kind === 'template') {
                setSelectedVersion(null);
                onTemplateSelect?.();
                return;
              }
              if (menu?.payload?.kind === 'backtest') {
                handleSelectVersion(menu.payload.backtest);
              }
            },
          },
          {
            id: 'rerun-entry',
            label: '作为重新回测入口',
            icon: <RotateCcw size={14} />,
            onSelect: () => {
              if (menu?.payload?.kind === 'template') {
                setSelectedVersion(null);
                onTemplateSelect?.();
                return;
              }
              if (menu?.payload?.kind === 'backtest') {
                handleSelectVersion(menu.payload.backtest);
              }
            },
          },
          { id: 'sep-copy', type: 'separator' },
          {
            id: 'copy-run-id',
            label: '复制运行 ID',
            icon: <Copy size={14} />,
            onSelect: () => copyText(runId),
          },
          {
            id: 'copy-backtest-id',
            label: '复制回测 ID',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'backtest',
            onSelect: () =>
              copyText(
                menu?.payload?.kind === 'backtest'
                  ? menu.payload.backtest.id
                  : undefined
              ),
          },
          {
            id: 'copy-version',
            label: '复制版本号',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'backtest',
            onSelect: () =>
              copyText(
                menu?.payload?.kind === 'backtest'
                  ? menu.payload.backtest.version
                  : undefined
              ),
          },
          { id: 'sep-danger', type: 'separator' },
          {
            id: 'delete',
            label: '删除回测版本...',
            danger: true,
            icon: <Trash2 size={14} />,
            disabled:
              menu?.payload?.kind !== 'backtest' ||
              menu.payload.backtest.status.toUpperCase() === 'RUNNING',
            onSelect: () => {
              if (menu?.payload?.kind === 'backtest') {
                setDeleteError(null);
                setDeleteTarget(menu.payload.backtest);
              }
            },
          },
        ]}
      />
    </>
  );
}

import {
  ArrowLeft,
  Play,
  Pause,
  Settings,
  TrendingUp,
  Clock,
  CalendarDays,
  Target,
  BarChart2,
  Bot,
  Activity,
  LineChart,
  TestTube,
  Rocket,
  History,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  GitCommitHorizontal,
  Boxes,
  Trash2,
  Square,
  BookOpen,
  type LucideIcon,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useState,
  useMemo,
  useRef,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';
import type { DateRange } from 'react-day-picker';
import { useQuery, useMutation } from 'urql';
import { Link, useParams, useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  StartStrategyDocument,
  StrategyDocument,
  StopStrategyDocument,
  StrategyRunStatus,
  StrategyRunMode,
} from '@/generated/gql/graphql';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import BacktestHistoryTab from '../components/BacktestHistoryTab';
import BucketLedgerTab from '../components/BucketLedgerTab';
import DecisionAuditTab from '../components/DecisionAuditTab';
import ExecutionTraceTab from '../components/ExecutionTraceTab';
import GridBookTab from '../components/GridBookTab';
import LimitUpBoardWorkbench from '../components/LimitUpBoardWorkbench';
import PerformanceTab from '../components/PerformanceTab';
import type { StrategyStudioMode } from '../components/strategy-studio-modes';
import StrategyConfigTab from '../components/StrategyConfigTab';
import StrategyLogsTab from '../components/StrategyLogsTab';
import StrategyMonitor from '../components/StrategyMonitor';
import StrategyOverviewTab from '../components/StrategyOverviewTab';
import { StrategyStudioShell } from '../components/StrategyStudioShell';
import {
  mapBucketLedgerView,
  mapExecutionTraceView,
  mapStrategyDecisionView,
  mapStrategyInstanceView,
  getStrategyRunState,
  type StrategyRunActionId,
  type StrategyInstance,
  type StrategyJsonValue,
} from '../domain';
import { toBacktestBoundaryIso } from '../domain/backtestRange';
import {
  BacktestHistoryQuery,
  DeleteStrategyRunMutation,
  PauseStrategyInstanceMutation,
  RerunBacktestVersionMutation,
  ResumeStrategyInstanceMutation,
  StrategyBucketLedgerQuery,
  StrategyDecisionHistoryQuery,
  StrategyExecutionTraceQuery,
  StrategyInstanceQuery,
  StrategyInstancesQuery,
} from '../hooks/strategyInstanceOperations';

type BacktestHistoryRecord = {
  id: string;
  version: number;
  parameters?: Record<string, StrategyJsonValue>;
  instruments?: string[];
  backtestStartTime?: string | null;
  backtestEndTime?: string | null;
  status?: string | null;
  metrics?: Record<string, StrategyJsonValue> | null;
  errorMessage?: string | null;
  resultPath?: string | null;
  createdAt?: string | null;
};

function asParameterRecord(value: unknown): Record<string, StrategyJsonValue> {
  if (!value) return {};
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return asParameterRecord(parsed);
    } catch {
      return {};
    }
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, StrategyJsonValue>;
  }
  return {};
}

function readString(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return undefined;
}

function readNumber(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

type PaperAccountSnapshotDraft = {
  instrumentCode: string;
  initialCash: string;
  positionShares: string;
  avgCost: string;
};

function toInputNumberValue(value?: number) {
  return value === undefined ? '' : String(value);
}

function toNonNegativeNumber(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function toPositiveNumber(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function toNonNegativeInteger(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0
    ? Math.floor(parsed)
    : undefined;
}

function buildPaperAccountSnapshotDraft(
  displayRun:
    | {
        instruments?: string[];
        parameters?: Record<string, StrategyJsonValue>;
      }
    | null
    | undefined,
  instance?: StrategyInstance | null
): PaperAccountSnapshotDraft {
  const parameters = {
    ...asParameterRecord(displayRun?.parameters),
    ...asParameterRecord(instance?.parameters),
  } as Record<string, unknown>;
  const bucketShares = [
    readNumber(parameters, ['locked_core_shares', 'lockedCoreShares']),
    readNumber(parameters, ['core_shares', 'coreShares']),
    readNumber(parameters, ['swing_shares', 'swingShares']),
  ];
  const derivedPositionShares = bucketShares.some(value => value !== undefined)
    ? bucketShares.reduce<number>((sum, value) => sum + (value ?? 0), 0)
    : undefined;

  const instrumentCode =
    instance?.instrumentCode ||
    displayRun?.instruments?.[0] ||
    readString(parameters, ['instrument_code', 'instrumentCode', 'symbol']) ||
    '-';
  const initialCash = readNumber(parameters, [
    'cash_total',
    'cashTotal',
    'available_cash',
    'availableCash',
    'initial_capital',
    'initialCapital',
  ]);
  const positionShares =
    readNumber(parameters, ['position_shares', 'positionShares']) ??
    derivedPositionShares;
  const avgCost = readNumber(parameters, [
    'avg_cost',
    'avgCost',
    'long_avg_price',
    'longAvgPrice',
    'base_price',
    'basePrice',
  ]);

  return {
    instrumentCode,
    initialCash: toInputNumberValue(initialCash),
    positionShares: toInputNumberValue(positionShares ?? 0),
    avgCost: toInputNumberValue(avgCost),
  };
}

function buildPaperParameterOverrides(
  draft: PaperAccountSnapshotDraft,
  sourceParameters: Record<string, StrategyJsonValue>
) {
  const parameters = sourceParameters as Record<string, unknown>;
  const overrides: Record<string, StrategyJsonValue> = {};
  const initialCash = toPositiveNumber(draft.initialCash);
  if (initialCash !== undefined) {
    overrides.initial_capital = initialCash;
    overrides.cash_total = initialCash;
  }

  const positionShares = toNonNegativeInteger(draft.positionShares);
  if (positionShares !== undefined) {
    const lockedSource =
      readNumber(parameters, ['locked_core_shares', 'lockedCoreShares']) ?? 0;
    const swingSource =
      readNumber(parameters, ['swing_shares', 'swingShares']) ?? 0;
    const lockedCoreShares = Math.min(
      Math.max(0, Math.floor(lockedSource)),
      positionShares
    );
    const swingShares = Math.min(
      Math.max(0, Math.floor(swingSource)),
      Math.max(0, positionShares - lockedCoreShares)
    );
    overrides.position_shares = positionShares;
    overrides.locked_core_shares = lockedCoreShares;
    overrides.swing_shares = swingShares;
    overrides.core_shares = Math.max(
      0,
      positionShares - lockedCoreShares - swingShares
    );
  }

  const avgCost = toNonNegativeNumber(draft.avgCost);
  if (avgCost !== undefined) {
    overrides.avg_cost = avgCost;
    const basePrice = readNumber(parameters, ['base_price', 'basePrice']);
    if (!basePrice || basePrice <= 0) {
      overrides.base_price = avgCost;
    }
  }

  return overrides;
}

function PaperAccountSnapshotEditor({
  draft,
  onChange,
}: {
  draft: PaperAccountSnapshotDraft;
  onChange: (draft: PaperAccountSnapshotDraft) => void;
}) {
  const fields = [
    {
      key: 'initialCash' as const,
      label: '初始可用资金',
      suffix: '元',
      step: '1000',
      min: '1',
    },
    {
      key: 'positionShares' as const,
      label: '初始持仓',
      suffix: '股',
      step: '100',
      min: '0',
    },
    {
      key: 'avgCost' as const,
      label: '成本价',
      suffix: '元',
      step: '0.01',
      min: '0',
    },
  ];

  return (
    <div className="space-y-3">
      <p className="leading-relaxed">
        将创建一个独立模拟账户快照，不连接真实账户，也不复用其他模拟盘账户。
      </p>
      <div className="rounded-panel border border-slate-200 bg-slate-50 p-3 dark:border-white/10 dark:bg-white/[0.03]">
        <div className="mb-3 grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <div className="text-ui-caption font-bold text-slate-400">
              绑定标的
            </div>
            <div className="text-ui-label font-bold text-slate-900 dark:text-white">
              {draft.instrumentCode}
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-ui-caption font-bold text-slate-400">
              账户模型
            </div>
            <div className="text-ui-label font-bold text-slate-900 dark:text-white">
              独立快照
            </div>
          </div>
        </div>
        <div className="grid gap-3">
          {fields.map(field => (
            <div key={field.key} className="space-y-1.5">
              <Label className="text-ui-caption font-bold text-slate-400">
                {field.label}
              </Label>
              <div className="relative">
                <Input
                  type="number"
                  min={field.min}
                  step={field.step}
                  value={draft[field.key]}
                  onChange={event =>
                    onChange({
                      ...draft,
                      [field.key]: event.target.value,
                    })
                  }
                  className="h-9 rounded-lg border-slate-200 bg-white pr-10 text-ui-label font-bold tabular-nums dark:border-white/10 dark:bg-slate-950/40"
                />
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ui-caption font-bold text-slate-400">
                  {field.suffix}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <p className="text-ui-label leading-relaxed text-slate-500 dark:text-slate-400">
        创建后不会自动启动；启动模拟盘时只使用这份虚拟资金和持仓快照。
      </p>
    </div>
  );
}

function formatDateOnly(value?: string | Date | number | null) {
  if (!value) return null;
  if (typeof value === 'string') {
    const match = value.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (match) return `${match[1]}/${Number(match[2])}/${Number(match[3])}`;
  }

  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('zh-CN');
}

function formatBacktestRangeLabel(
  range?: {
    startTime?: string | null;
    endTime?: string | null;
  } | null
) {
  const start = formatDateOnly(range?.startTime);
  const end = formatDateOnly(range?.endTime);
  if (!start || !end) return null;
  return `${start} - ${end}`;
}

function parseBacktestDate(value?: string | Date | number | null) {
  if (!value) return undefined;
  if (value instanceof Date) {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate());
  }

  const text = String(value);
  const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (match) {
    const date = new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3])
    );
    return Number.isNaN(date.getTime()) ? undefined : date;
  }

  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return undefined;
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function getActionIcon(action: StrategyRunActionId) {
  if (action === 'delete') return Trash2;
  if (
    action === 'stop_backtest' ||
    action === 'stop_paper' ||
    action === 'stop_live'
  ) {
    return Square;
  }
  if (action === 'pause_paper' || action === 'pause_live') {
    return Pause;
  }
  if (
    action === 'clone_to_paper' ||
    action === 'clone_paper' ||
    action === 'start_paper'
  ) {
    return TestTube;
  }
  if (action === 'clone_to_live' || action === 'start_live') return Rocket;
  if (action === 'edit_parameters') return Settings;
  if (action === 'view_logs' || action === 'view_error') return Clock;
  if (action === 'view_performance') return BarChart2;
  return Play;
}

function getPrimaryActionClass(tone?: string) {
  switch (tone) {
    case 'emerald':
      return 'bg-emerald-600 hover:bg-emerald-500 shadow-emerald-600/20';
    case 'amber':
      return 'bg-amber-500 hover:bg-amber-400 shadow-amber-500/20 text-slate-950';
    case 'rose':
      return 'bg-rose-600 hover:bg-rose-500 shadow-rose-600/20';
    case 'purple':
      return 'bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/20';
    case 'slate':
      return 'bg-slate-700 hover:bg-slate-600 shadow-slate-700/20';
    case 'red':
    default:
      return 'bg-red-600 hover:bg-red-500 shadow-red-600/20';
  }
}

type StrategyRunTabItem = {
  id: string;
  name: string;
  icon: LucideIcon;
};

type ScrollDirection = 'previous' | 'next';

function ScrollableStrategyTabs({
  tabs,
  activeTab,
}: {
  tabs: StrategyRunTabItem[];
  activeTab: string;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({
    pointerId: -1,
    startX: 0,
    scrollLeft: 0,
    dragged: false,
  });
  const suppressClickRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);
  const [canScrollPrevious, setCanScrollPrevious] = useState(false);
  const [canScrollNext, setCanScrollNext] = useState(false);

  const getTabElements = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return [];
    return Array.from(
      viewport.querySelectorAll<HTMLElement>('[data-strategy-run-tab]')
    );
  }, []);

  const updateScrollState = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const maxScrollLeft = Math.max(
      0,
      viewport.scrollWidth - viewport.clientWidth
    );
    setCanScrollPrevious(viewport.scrollLeft > 1);
    setCanScrollNext(viewport.scrollLeft < maxScrollLeft - 1);
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    updateScrollState();
    viewport.addEventListener('scroll', updateScrollState, { passive: true });

    const resizeObserver =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(updateScrollState)
        : null;
    resizeObserver?.observe(viewport);
    if (viewport.firstElementChild) {
      resizeObserver?.observe(viewport.firstElementChild);
    }

    window.addEventListener('resize', updateScrollState);
    return () => {
      viewport.removeEventListener('scroll', updateScrollState);
      resizeObserver?.disconnect();
      window.removeEventListener('resize', updateScrollState);
    };
  }, [tabs.length, updateScrollState]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || isDragging) return;

    const activeElement = viewport.querySelector<HTMLElement>(
      `[data-strategy-run-tab="${activeTab}"]`
    );
    if (!activeElement) return;

    const visibleStart = viewport.scrollLeft;
    const visibleEnd = visibleStart + viewport.clientWidth;
    const itemStart = activeElement.offsetLeft;
    const itemEnd = itemStart + activeElement.offsetWidth;

    if (itemStart < visibleStart) {
      viewport.scrollTo({ left: itemStart, behavior: 'smooth' });
    } else if (itemEnd > visibleEnd) {
      viewport.scrollTo({
        left: itemEnd - viewport.clientWidth,
        behavior: 'smooth',
      });
    }
  }, [activeTab, isDragging]);

  const scrollToTab = useCallback(
    (direction: ScrollDirection) => {
      const viewport = viewportRef.current;
      if (!viewport) return;

      const tabElements = getTabElements();
      if (!tabElements.length) return;

      const visibleStart = viewport.scrollLeft;
      const visibleEnd = visibleStart + viewport.clientWidth;
      const maxScrollLeft = Math.max(
        0,
        viewport.scrollWidth - viewport.clientWidth
      );

      let targetLeft = visibleStart;
      if (direction === 'next') {
        const nextTab = tabElements.find(
          tab => tab.offsetLeft + tab.offsetWidth > visibleEnd + 1
        );
        targetLeft = nextTab
          ? nextTab.offsetLeft + nextTab.offsetWidth - viewport.clientWidth
          : maxScrollLeft;
      } else {
        const previousTab = [...tabElements]
          .reverse()
          .find(tab => tab.offsetLeft < visibleStart - 1);
        targetLeft = previousTab ? previousTab.offsetLeft : 0;
      }

      viewport.scrollTo({
        left: Math.max(0, Math.min(targetLeft, maxScrollLeft)),
        behavior: 'smooth',
      });
    },
    [getTabElements]
  );

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const viewport = viewportRef.current;
    if (!viewport || (event.pointerType === 'mouse' && event.button !== 0)) {
      return;
    }

    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      scrollLeft: viewport.scrollLeft,
      dragged: false,
    };
    setIsDragging(true);
    viewport.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const viewport = viewportRef.current;
    const drag = dragRef.current;
    if (!viewport || drag.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - drag.startX;
    if (Math.abs(deltaX) > 4) {
      drag.dragged = true;
      suppressClickRef.current = true;
      event.preventDefault();
      viewport.scrollLeft = drag.scrollLeft - deltaX;
    }
  };

  const finishDragging = (event: ReactPointerEvent<HTMLDivElement>) => {
    const viewport = viewportRef.current;
    const drag = dragRef.current;
    if (!viewport || drag.pointerId !== event.pointerId) return;

    if (viewport.hasPointerCapture(event.pointerId)) {
      viewport.releasePointerCapture(event.pointerId);
    }
    dragRef.current.pointerId = -1;
    setIsDragging(false);
    if (drag.dragged) {
      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    }
  };

  const handleClickCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!suppressClickRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    suppressClickRef.current = false;
  };

  const hasOverflow = canScrollPrevious || canScrollNext;

  return (
    <div className="flex w-full items-center gap-1">
      {hasOverflow && (
        <button
          type="button"
          aria-label="向左显示上一个标签"
          disabled={!canScrollPrevious}
          onClick={() => scrollToTab('previous')}
          className="flex h-8 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-slate-300 shadow-inner transition-colors hover:border-red-400/40 hover:bg-red-500/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-white/10 disabled:hover:bg-white/[0.04] disabled:hover:text-slate-300"
        >
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
      )}
      <div
        ref={viewportRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishDragging}
        onPointerCancel={finishDragging}
        onClickCapture={handleClickCapture}
        className={cn(
          'min-w-0 flex-1 overflow-x-auto overflow-y-hidden [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
          isDragging ? 'cursor-grabbing select-none' : 'cursor-grab'
        )}
      >
        <TabsList className="h-auto w-max min-w-max justify-start gap-ui-panel bg-transparent p-0 sm:gap-ui-panel">
          {tabs.map(tab => {
            const TabIcon = tab.icon;
            return (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                data-strategy-run-tab={tab.id}
                className="group relative shrink-0 rounded-none bg-transparent px-0 py-ui-section text-ui-label font-bold text-slate-500 transition-colors hover:bg-transparent hover:text-slate-300 data-[state=active]:bg-transparent data-[state=active]:text-red-400 data-[state=active]:shadow-none"
              >
                <div className="flex items-center gap-2">
                  <TabIcon
                    size={14}
                    className="group-hover:text-slate-300 group-data-[state=active]:text-red-400"
                    aria-hidden="true"
                  />
                  {tab.name}
                </div>
                <span className="absolute bottom-0 left-0 h-[2px] w-full scale-x-0 bg-red-500 transition-transform duration-300 group-data-[state=active]:scale-x-100" />
              </TabsTrigger>
            );
          })}
        </TabsList>
      </div>
      {hasOverflow && (
        <button
          type="button"
          aria-label="向右显示下一个标签"
          disabled={!canScrollNext}
          onClick={() => scrollToTab('next')}
          className="flex h-8 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-slate-300 shadow-inner transition-colors hover:border-red-400/40 hover:bg-red-500/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-white/10 disabled:hover:bg-white/[0.04] disabled:hover:text-slate-300"
        >
          <ChevronRight size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

function getTagClass(tone?: string) {
  switch (tone) {
    case 'emerald':
      return 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400';
    case 'amber':
      return 'bg-amber-500/10 border-amber-500/20 text-amber-400';
    case 'rose':
      return 'bg-rose-500/10 border-rose-500/20 text-rose-400';
    case 'purple':
      return 'bg-purple-500/10 border-purple-500/20 text-purple-400';
    case 'red':
      return 'bg-red-500/10 border-red-500/20 text-red-400';
    default:
      return 'bg-slate-500/10 border-slate-500/20 text-slate-400';
  }
}

function getBacktestStatusLabel(status?: string | null) {
  switch ((status || '').toUpperCase()) {
    case 'COMPLETED':
      return '已完成';
    case 'RUNNING':
      return '运行中';
    case 'ERROR':
      return '错误';
    case 'PENDING':
      return '等待中';
    default:
      return status || '-';
  }
}

export default function StrategyDetailPage() {
  const { strategyId, runId } = useParams();
  const id = parseInt(strategyId || '0', 10);
  const queryRunId = new URLSearchParams(window.location.search).get('runId');
  const selectedRunId = runId || queryRunId;
  const [, setLocation] = useLocation();
  const [activeTab, setActiveTab] = useState('overview');

  useEffect(() => {
    if (!runId && queryRunId && id) {
      setLocation(`/strategies/${id}/runs/${encodeURIComponent(queryRunId)}`, {
        replace: true,
      });
    }
  }, [id, queryRunId, runId, setLocation]);

  // Confirm dialog state
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    title: string;
    description: ReactNode;
    paperSnapshotDraft?: PaperAccountSnapshotDraft;
    confirmText?: string;
    variant?: 'default' | 'destructive';
    onConfirm: () => Promise<void> | void;
  }>({ open: false, title: '', description: '', onConfirm: () => {} });
  const paperSnapshotDraftRef = useRef<PaperAccountSnapshotDraft | null>(null);
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false);
  const [rerunDateRange, setRerunDateRange] = useState<DateRange>();
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [rerunSubmitting, setRerunSubmitting] = useState(false);
  const [selectedBacktestId, setSelectedBacktestId] = useState<string | null>(
    null
  );

  const openConfirm = (
    title: string,
    description: ReactNode,
    onConfirm: () => Promise<void> | void,
    variant?: 'default' | 'destructive',
    confirmText = '确认',
    paperSnapshotDraft?: PaperAccountSnapshotDraft
  ) => {
    paperSnapshotDraftRef.current = paperSnapshotDraft || null;
    setConfirmDialog({
      open: true,
      title,
      description,
      onConfirm,
      variant,
      confirmText,
      paperSnapshotDraft,
    });
  };

  const updatePaperSnapshotDraft = (draft: PaperAccountSnapshotDraft) => {
    paperSnapshotDraftRef.current = draft;
    setConfirmDialog(prev => ({ ...prev, paperSnapshotDraft: draft }));
  };

  const [, startStrategy] = useMutation(StartStrategyDocument);
  const [, stopStrategy] = useMutation(StopStrategyDocument);
  const [, pauseStrategy] = useMutation(PauseStrategyInstanceMutation);
  const [, resumeStrategy] = useMutation(ResumeStrategyInstanceMutation);
  const [, rerunBacktestVersion] = useMutation(RerunBacktestVersionMutation);
  const [, deleteStrategyRun] = useMutation(DeleteStrategyRunMutation);

  // 获取策略模版详情
  const [{ data: strategyData, fetching: strategyLoading }] = useQuery({
    query: StrategyDocument,
    variables: { strategyId: id },
    pause: !id,
  });

  const [{ data: instancesData, fetching: runsLoading }, reexecuteRuns] =
    useQuery({
      query: StrategyInstancesQuery,
      variables: {
        strategyKey: String(id),
        status: null,
        instrumentCode: null,
      },
      requestPolicy: 'cache-and-network',
    });

  const [{ data: selectedInstanceData, fetching: selectedInstanceLoading }] =
    useQuery({
      query: StrategyInstanceQuery,
      variables: { id: selectedRunId || '' },
      pause: !selectedRunId,
      requestPolicy: 'cache-and-network',
    });

  const strategy = strategyData?.strategy;

  const selectedInstanceFromRoute = useMemo(() => {
    const raw = selectedInstanceData?.strategyInstance;
    return raw ? mapStrategyInstanceView(raw) : null;
  }, [selectedInstanceData]);

  const strategyInstances: StrategyInstance[] = useMemo(() => {
    const instances = (
      (instancesData?.strategyInstances || []) as unknown[]
    ).map(mapStrategyInstanceView);

    if (!selectedInstanceFromRoute) return instances;

    const existingIndex = instances.findIndex(
      instance => instance.id === selectedInstanceFromRoute.id
    );
    if (existingIndex < 0) {
      return [selectedInstanceFromRoute, ...instances];
    }

    const merged = [...instances];
    merged[existingIndex] = selectedInstanceFromRoute;
    return merged;
  }, [instancesData, selectedInstanceFromRoute]);

  const strategyRuns = useMemo(
    () =>
      strategyInstances
        .map(instance => ({
          id: instance.id,
          name: instance.displayName,
          strategy: {
            id: instance.strategyId || id,
            name: instance.strategyKey,
          },
          instruments: [instance.instrumentCode],
          parameters: instance.parameters,
          mode: instance.mode as StrategyRunMode,
          status: instance.status as StrategyRunStatus,
          profitLoss: 0,
          totalTrades: 0,
          metrics: {},
          errorMessage: null,
          createTime: instance.createdAt,
          startTime: instance.createdAt,
          stopTime: null,
          updatedAt: instance.updatedAt,
          lastDecisionAt: instance.lastDecisionAt,
        }))
        .sort((a, b) => {
          const bTime = Date.parse(
            b.lastDecisionAt || b.updatedAt || b.createTime || ''
          );
          const aTime = Date.parse(
            a.lastDecisionAt || a.updatedAt || a.createTime || ''
          );
          const normalizedBTime = Number.isFinite(bTime) ? bTime : 0;
          const normalizedATime = Number.isFinite(aTime) ? aTime : 0;

          if (normalizedBTime !== normalizedATime) {
            return normalizedBTime - normalizedATime;
          }

          return String(b.id).localeCompare(String(a.id));
        }),
    [strategyInstances, id]
  );

  // 查找该策略的活跃运行实例
  const activeRun = strategyRuns.find(r =>
    ['RUNNING', 'PAUSED'].includes(getStrategyRunState(r.mode, r.status).status)
  );

  // 获取最近一次运行（包含已停止/完成的），用于显示状态和重启
  const latestRun = useMemo(() => {
    if (!strategyRuns.length) return null;
    return [...strategyRuns].sort((a, b) => {
      return (b.createTime || '').localeCompare(a.createTime || '');
    })[0];
  }, [strategyRuns]);

  const selectedRun = useMemo(
    () =>
      selectedRunId
        ? strategyRuns.find(run => run.id === selectedRunId) || null
        : null,
    [strategyRuns, selectedRunId]
  );

  // 带 runId 进入详情页时只显示该实例，避免缓存列表缺失时串到其它实例。
  const displayRun = selectedRunId ? selectedRun : activeRun || latestRun;
  const strategyInstance =
    strategyInstances.find(instance => instance.id === displayRun?.id) || null;

  useEffect(() => {
    setSelectedBacktestId(null);
  }, [displayRun?.id]);

  const shouldLoadDecisionHistory =
    !!displayRun?.id &&
    (activeTab === 'audit' || activeTab === 'monitor' || activeTab === 'board');
  const shouldLoadExecutionTrace =
    !!displayRun?.id &&
    (activeTab === 'execution' ||
      activeTab === 'monitor' ||
      activeTab === 'board');
  const shouldLoadBucketLedger = !!displayRun?.id && activeTab === 'buckets';

  const [{ data: decisionData }, reexecuteDecisionHistory] = useQuery({
    query: StrategyDecisionHistoryQuery,
    variables: {
      instanceId: displayRun?.id || '',
      cursor: null,
      limit: 200,
      backtestId:
        displayRun?.mode === StrategyRunMode.Backtest
          ? selectedBacktestId
          : null,
    },
    pause: !shouldLoadDecisionHistory,
    requestPolicy: 'cache-and-network',
  });
  const [{ data: executionData }, reexecuteExecutionTrace] = useQuery({
    query: StrategyExecutionTraceQuery,
    variables: {
      instanceId: displayRun?.id || '',
      decisionId: null,
      backtestId:
        displayRun?.mode === StrategyRunMode.Backtest
          ? selectedBacktestId
          : null,
      cursor: null,
      limit: 200,
    },
    pause: !shouldLoadExecutionTrace,
    requestPolicy: 'cache-and-network',
  });
  const [{ data: bucketData }] = useQuery({
    query: StrategyBucketLedgerQuery,
    variables: { instanceId: displayRun?.id || '' },
    pause: !shouldLoadBucketLedger,
    requestPolicy: 'cache-and-network',
  });
  const [{ data: backtestHistoryData }, reexecuteBacktestHistory] = useQuery({
    query: BacktestHistoryQuery,
    variables: { runId: displayRun?.id || '' },
    pause: !displayRun?.id || displayRun?.mode !== StrategyRunMode.Backtest,
    requestPolicy: 'cache-and-network',
  });

  const decisionHistory = useMemo(
    () =>
      ((decisionData?.strategyDecisionHistory || []) as unknown[]).map(
        mapStrategyDecisionView
      ),
    [decisionData]
  );
  const executionTrace = useMemo(
    () =>
      ((executionData?.strategyExecutionTrace || []) as unknown[]).map(
        mapExecutionTraceView
      ),
    [executionData]
  );

  useEffect(() => {
    const isLiveMonitor =
      (activeTab === 'monitor' || activeTab === 'board') &&
      !!displayRun?.id &&
      displayRun.mode !== StrategyRunMode.Backtest &&
      displayRun.status === StrategyRunStatus.Running;

    if (!isLiveMonitor) return;

    const refreshMonitorEvents = () => {
      if (document.visibilityState !== 'visible') return;
      reexecuteDecisionHistory({ requestPolicy: 'network-only' });
      reexecuteExecutionTrace({ requestPolicy: 'network-only' });
    };

    refreshMonitorEvents();
    const interval = window.setInterval(refreshMonitorEvents, 5000);
    document.addEventListener('visibilitychange', refreshMonitorEvents);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', refreshMonitorEvents);
    };
  }, [
    activeTab,
    displayRun?.id,
    displayRun?.mode,
    displayRun?.status,
    reexecuteDecisionHistory,
    reexecuteExecutionTrace,
  ]);
  const bucketLedger = useMemo(
    () => mapBucketLedgerView(bucketData?.strategyBucketLedger),
    [bucketData]
  );
  const backtestHistory = useMemo<BacktestHistoryRecord[]>(
    () =>
      ((backtestHistoryData?.backtestHistory || []) as unknown[]).map(raw => {
        const record = (raw || {}) as Record<string, unknown>;
        return {
          id: String(record.id || ''),
          version: Number(record.version || 0),
          parameters: asParameterRecord(record.parameters),
          instruments: Array.isArray(record.instruments)
            ? record.instruments.map(item => String(item))
            : [],
          backtestStartTime:
            readString(record, ['backtestStartTime', 'backtest_start_time']) ??
            null,
          backtestEndTime:
            readString(record, ['backtestEndTime', 'backtest_end_time']) ??
            null,
          status: readString(record, ['status']) ?? null,
          metrics: asParameterRecord(record.metrics),
          errorMessage:
            readString(record, ['errorMessage', 'error_message']) ?? null,
          resultPath: readString(record, ['resultPath', 'result_path']) ?? null,
          createdAt: readString(record, ['createdAt', 'created_at']) ?? null,
        };
      }),
    [backtestHistoryData]
  );
  const latestBacktestVersion = useMemo(() => {
    if (!backtestHistory.length) return null;
    return [...backtestHistory].sort((a, b) => {
      if (a.version !== b.version) return b.version - a.version;
      return (b.createdAt || '').localeCompare(a.createdAt || '');
    })[0];
  }, [backtestHistory]);
  const currentBacktestVersion = useMemo(() => {
    if (!backtestHistory.length) return null;
    if (selectedBacktestId) {
      const selected = backtestHistory.find(
        backtest => backtest.id === selectedBacktestId
      );
      if (selected) return selected;
    }
    return latestBacktestVersion;
  }, [backtestHistory, latestBacktestVersion, selectedBacktestId]);

  useEffect(() => {
    if (
      selectedBacktestId &&
      !backtestHistory.some(backtest => backtest.id === selectedBacktestId)
    ) {
      setSelectedBacktestId(null);
    }
  }, [backtestHistory, selectedBacktestId]);

  const backtestMonitorRange = useMemo(() => {
    if (displayRun?.mode !== StrategyRunMode.Backtest) return null;
    const parameters = (strategyInstance?.parameters ||
      displayRun.parameters ||
      {}) as Record<string, unknown>;
    const startTime =
      currentBacktestVersion?.backtestStartTime ||
      readString(parameters, [
        'backtestStartTime',
        'backtest_start_time',
        'startTime',
        'start_time',
      ]) ||
      null;
    const endTime =
      currentBacktestVersion?.backtestEndTime ||
      readString(parameters, [
        'backtestEndTime',
        'backtest_end_time',
        'endTime',
        'end_time',
      ]) ||
      null;

    return { startTime, endTime };
  }, [currentBacktestVersion, displayRun, strategyInstance]);
  const backtestRangeLabel = useMemo(
    () => formatBacktestRangeLabel(backtestMonitorRange),
    [backtestMonitorRange]
  );

  const CloneStrategyMutation = `
    mutation CloneStrategy(
      $runId: String!
      $targetMode: StrategyRunMode!
      $parameterOverrides: JSON
    ) {
      cloneStrategy(
        runId: $runId
        targetMode: $targetMode
        parameterOverrides: $parameterOverrides
      ) {
        success
        message
      }
    }
  `;
  const [, cloneStrategy] = useMutation(CloneStrategyMutation);

  // 模拟统计数据（使用 displayRun）
  const displayMetrics = asParameterRecord(displayRun?.metrics);
  const totalPnl = Number(displayMetrics.totalPnl ?? 0);
  const stats = [
    {
      label: '累计收益',
      value: `${displayRun ? totalPnl.toFixed(2) : '0.00'}`,
      // 注意：metrics 结构可能根据 generated 类型不同，这里做保护性访问
      // 如果 displayRun.metrics 是 JSON/any，则直接访问
      icon: TrendingUp,
      color: financialToneClass(totalPnl),
    },
    {
      label:
        displayRun?.mode === StrategyRunMode.Backtest ? '回测版本' : '运行时间',
      value:
        displayRun?.mode === StrategyRunMode.Backtest
          ? currentBacktestVersion
            ? `v${currentBacktestVersion.version}`
            : '-'
          : displayRun?.startTime
            ? getTimeDuration(displayRun.startTime, displayRun.stopTime)
            : '-',
      icon: displayRun?.mode === StrategyRunMode.Backtest ? History : Clock,
      color: 'text-red-500',
    },
    {
      label: '绑定标的',
      value: strategyInstance?.instrumentCode || '-',
      icon: Target,
      color: 'text-purple-500',
    },
    {
      label:
        displayRun?.mode === StrategyRunMode.Backtest ? '回测区间' : '日均意图',
      value:
        displayRun?.mode === StrategyRunMode.Backtest
          ? backtestRangeLabel || '-'
          : String(displayMetrics.dailyIntentCount ?? '-'),
      icon:
        displayRun?.mode === StrategyRunMode.Backtest
          ? CalendarDays
          : BarChart2,
      color:
        displayRun?.mode === StrategyRunMode.Backtest
          ? 'text-red-600'
          : 'text-amber-500',
    },
  ];

  const refreshRuns = () => {
    reexecuteRuns({ requestPolicy: 'network-only' });
  };

  const closeConfirm = () => {
    setConfirmDialog(prev => ({ ...prev, open: false }));
  };

  const openRerunBacktestDialog = () => {
    setRerunDateRange({
      from: parseBacktestDate(backtestMonitorRange?.startTime),
      to: parseBacktestDate(backtestMonitorRange?.endTime),
    });
    setRerunError(null);
    setRerunDialogOpen(true);
  };

  const createBacktestVersion = async () => {
    if (!displayRun) return;
    const from = rerunDateRange?.from;
    const to = rerunDateRange?.to;
    if (!from || !to) {
      setRerunError('请选择完整的回测开始日期和结束日期。');
      return;
    }
    if (to.getTime() < from.getTime()) {
      setRerunError('回测结束日期不能早于开始日期。');
      return;
    }

    setRerunSubmitting(true);
    setRerunError(null);
    try {
      const result = await rerunBacktestVersion({
        runId: displayRun.id,
        backtestStartTime: toBacktestBoundaryIso(from, 'start'),
        backtestEndTime: toBacktestBoundaryIso(to, 'end'),
      });
      if (result.error) {
        throw new Error(result.error.message);
      }
      refreshRuns();
      reexecuteBacktestHistory({ requestPolicy: 'network-only' });
      setRerunDialogOpen(false);
      setActiveTab('history');
    } catch (error) {
      setRerunError(
        error instanceof Error ? error.message : '创建新回测版本失败。'
      );
    } finally {
      setRerunSubmitting(false);
    }
  };

  const cloneToMode = async (
    targetMode: StrategyRunMode,
    parameterOverrides?: Record<string, StrategyJsonValue>
  ) => {
    if (!displayRun) return;
    const result = await cloneStrategy({
      runId: displayRun.id,
      targetMode,
      parameterOverrides,
    });
    const newRunId = result.data?.cloneStrategy?.message;
    refreshRuns();
    closeConfirm();
    if (newRunId) {
      setLocation(`/strategies/${id}/runs/${encodeURIComponent(newRunId)}`);
    }
  };

  const deleteCurrentRun = async () => {
    if (!displayRun) return;
    await deleteStrategyRun({ runId: displayRun.id });
    closeConfirm();
    setLocation('/strategies');
  };

  const executeAction = async (action: StrategyRunActionId) => {
    if (!displayRun) {
      setLocation(`/strategies/${id}/run`);
      return;
    }

    if (action === 'edit_parameters') {
      setActiveTab('config');
      return;
    }
    if (action === 'view_logs' || action === 'view_error') {
      setActiveTab('logs');
      return;
    }
    if (action === 'view_performance') {
      setActiveTab('performance');
      return;
    }

    if (action === 'rerun_backtest') {
      openRerunBacktestDialog();
      return;
    }

    if (action === 'clone_to_paper' || action === 'clone_paper') {
      const draft = buildPaperAccountSnapshotDraft(
        displayRun,
        strategyInstance
      );
      openConfirm(
        '转为模拟盘',
        null,
        () => {
          const currentDraft = paperSnapshotDraftRef.current || draft;
          const sourceParameters = {
            ...asParameterRecord(displayRun.parameters),
            ...asParameterRecord(strategyInstance?.parameters),
          };
          return cloneToMode(
            StrategyRunMode.Paper,
            buildPaperParameterOverrides(currentDraft, sourceParameters)
          );
        },
        'default',
        '创建模拟盘',
        draft
      );
      return;
    }

    if (action === 'clone_to_live') {
      openConfirm(
        '转为实盘',
        '将以当前参数创建一个新的实盘实例。创建后不会自动启动，实盘启动仍需再次确认。',
        () => cloneToMode(StrategyRunMode.Live),
        'destructive'
      );
      return;
    }

    if (action === 'delete') {
      openConfirm(
        '确认删除',
        '确定要删除这个策略实例记录吗？此操作会清理关联的回测、策略意图和状态记录。',
        deleteCurrentRun,
        'destructive'
      );
      return;
    }

    if (action === 'start_live') {
      openConfirm(
        '确认启动实盘',
        '确认启动实盘实例？此操作可能连接真实交易环境并使用真实资金。',
        async () => {
          await startStrategy({ runId: displayRun.id });
          refreshRuns();
          closeConfirm();
        },
        'destructive'
      );
      return;
    }

    if (
      action === 'stop_live' ||
      action === 'stop_paper' ||
      action === 'stop_backtest'
    ) {
      openConfirm(
        action === 'stop_live' ? '确认停止实盘' : '确认停止实例',
        action === 'stop_live'
          ? '确认停止实盘实例？停止后需要重新创建或复制实例才能继续运行。'
          : '确认停止当前策略实例？',
        async () => {
          await stopStrategy({ runId: displayRun.id });
          refreshRuns();
          closeConfirm();
        },
        'destructive'
      );
      return;
    }

    if (action === 'start_backtest' || action === 'start_paper') {
      await startStrategy({ runId: displayRun.id });
    }
    if (action === 'pause_paper' || action === 'pause_live') {
      await pauseStrategy({ instanceId: displayRun.id });
    }
    if (
      action === 'resume_backtest' ||
      action === 'resume_paper' ||
      action === 'resume_live'
    ) {
      await resumeStrategy({ instanceId: displayRun.id });
    }
    refreshRuns();
  };

  // Helper for duration
  function getTimeDuration(start: string, end?: string | null) {
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : Date.now();
    const diffDays = Math.floor((e - s) / (1000 * 60 * 60 * 24));
    return `${diffDays} 天`;
  }

  const loadingSidebar = (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 px-ui-section py-3">
        <div className="text-ui-caption font-black uppercase tracking-[0.24em] text-red-400">
          Strategy Studio
        </div>
        <div className="mt-1 text-ui-label font-medium leading-relaxed text-slate-500">
          策略实例、模板和运行状态集中管理。
        </div>
      </div>

      <div className="border-b border-white/5 p-2">
        <div className="mb-2 px-2 text-ui-caption font-black uppercase tracking-[0.2em] text-slate-600">
          Workspace
        </div>
        <div className="space-y-1">
          {[
            { icon: Boxes, label: '策略看板', meta: '加载实例中' },
            { icon: BookOpen, label: '策略库', meta: 'Catalog' },
          ].map(item => {
            const Icon = item.icon;

            return (
              <div
                key={item.label}
                className="flex w-full items-center gap-3 rounded-md border border-transparent px-2.5 py-2 text-left text-slate-500"
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ui-label font-bold">
                    {item.label}
                  </span>
                  <span className="block truncate text-ui-caption font-medium text-slate-600">
                    {item.meta}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden p-2">
        <div className="mb-2 px-2 text-ui-caption font-black uppercase tracking-[0.2em] text-slate-600">
          Strategy Instances
        </div>
        <div className="space-y-2">
          {[1, 2, 3, 4].map(item => (
            <div
              key={item}
              className="h-[64px] animate-pulse rounded-md border border-white/5 bg-white/[0.03]"
            />
          ))}
        </div>
      </div>
    </aside>
  );

  const renderStudioMessage = (message: ReactNode, tone: 'rose' | 'slate') => (
    <StrategyStudioShell
      activeMode="MONITOR"
      className="h-full min-h-0"
      content={
        <div className="studio-workspace-surface flex h-full items-center justify-center p-ui-panel">
          <div
            className={cn(
              'rounded-lg border px-ui-section py-ui-section text-center text-ui-caption font-black uppercase tracking-widest',
              tone === 'rose'
                ? 'border-rose-500/20 bg-rose-500/10 text-rose-300'
                : 'border-white/10 bg-white/[0.03] text-slate-400'
            )}
          >
            {message}
          </div>
        </div>
      }
      onModeChange={mode => {
        if (mode === 'RUNS' || mode === 'CATALOG') setLocation('/strategies');
      }}
      sidebar={loadingSidebar}
      statusBarLeft={<span>策略详情</span>}
      statusBarRight={<span>{strategyId || '-'}</span>}
      tabBar={
        <div className="flex h-10 shrink-0 items-center border-b border-white/5 bg-[#0b1120]/80 px-3 text-ui-caption font-black uppercase tracking-[0.2em] text-slate-300">
          QuantX Strategy Studio
        </div>
      }
    />
  );

  if (strategyLoading || runsLoading || selectedInstanceLoading) {
    return renderStudioMessage('正在加载策略引擎...', 'slate');
  }

  if (!strategy) {
    return renderStudioMessage('策略不存在或已被移除', 'rose');
  }

  const runState = displayRun
    ? getStrategyRunState(displayRun.mode, displayRun.status)
    : null;
  const primaryAction = runState?.detailPrimaryAction;
  const secondaryActions = runState?.detailSecondaryActions || [];
  const isPullbackGridStrategy =
    strategy.name.includes('Pullback Grid') || strategy.name.includes('网格');
  const isLimitUpBoardStrategy =
    strategy.name.includes('打板') ||
    strategy.name.includes('Limit Up Board') ||
    strategy.name.includes('Limit-up Board');
  const strategyRunTabs: StrategyRunTabItem[] = [
    { id: 'monitor', name: '实时监控', icon: Activity },
    ...(isLimitUpBoardStrategy
      ? [{ id: 'board', name: '打板工作台', icon: Target }]
      : []),
    { id: 'overview', name: '实例概览', icon: Target },
    { id: 'audit', name: '决策审计', icon: ClipboardList },
    {
      id: 'execution',
      name: '执行跟踪',
      icon: GitCommitHorizontal,
    },
    ...(isPullbackGridStrategy
      ? [{ id: 'gridbook', name: '网格簿', icon: BookOpen }]
      : []),
    { id: 'buckets', name: '仓位归因', icon: Boxes },
    { id: 'logs', name: '执行日志', icon: Clock },
    { id: 'performance', name: '策略绩效', icon: BarChart2 },
    { id: 'config', name: '参数配置', icon: Settings },
    ...(displayRun?.mode === StrategyRunMode.Backtest
      ? [{ id: 'history', name: '回测版本', icon: History }]
      : []),
  ];
  const backtestStatusLabel = getBacktestStatusLabel(
    currentBacktestVersion?.status
  );
  const studioActiveMode: StrategyStudioMode =
    activeTab === 'history' || activeTab === 'performance'
      ? 'BACKTEST'
      : activeTab === 'audit' ||
          activeTab === 'execution' ||
          activeTab === 'logs'
        ? 'TRACE'
        : activeTab === 'config' ||
            activeTab === 'buckets' ||
            activeTab === 'gridbook'
          ? 'CONFIG'
          : activeTab === 'overview'
            ? 'RUNS'
            : 'MONITOR';

  const handleStudioModeChange = (mode: StrategyStudioMode) => {
    if (mode === 'CATALOG' || mode === 'RUNS') {
      setLocation('/strategies');
      return;
    }

    if (mode === 'MONITOR') {
      setActiveTab('monitor');
      return;
    }

    if (mode === 'TRACE') {
      setActiveTab('audit');
      return;
    }

    if (mode === 'BACKTEST') {
      setActiveTab(
        displayRun?.mode === StrategyRunMode.Backtest
          ? 'history'
          : 'performance'
      );
      return;
    }

    setActiveTab('config');
  };

  const openRunDashboard = (run: (typeof strategyRuns)[number]) => {
    setLocation(
      `/strategies/${run.strategy?.id}/runs/${encodeURIComponent(run.id)}`
    );
  };

  const detailSidebar = (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 px-ui-section py-3">
        <Link
          href="/strategies"
          className="mb-3 inline-flex h-7 items-center gap-2 rounded-md border border-white/10 px-2 text-ui-caption font-bold uppercase tracking-wider text-slate-400 transition-colors hover:border-red-500/40 hover:text-red-300"
        >
          <ArrowLeft className="h-3 w-3" />
          策略看板
        </Link>
        <div className="text-ui-caption font-black uppercase tracking-[0.24em] text-red-400">
          Strategy Dashboard
        </div>
        <h2 className="mt-1 truncate text-ui-body font-bold text-slate-100">
          {strategyInstance?.displayName || strategy.name}
        </h2>
        <p className="mt-1 line-clamp-2 text-ui-caption leading-relaxed text-slate-500">
          {strategy.description}
        </p>
      </div>

      <div className="border-b border-white/5 p-3">
        <div className="grid grid-cols-2 gap-2">
          {stats.map(stat => (
            <div
              key={stat.label}
              className="rounded-md border border-white/5 bg-white/[0.03] px-2 py-2"
            >
              <div className="truncate text-ui-micro font-black uppercase tracking-wider text-slate-600">
                {stat.label}
              </div>
              <div
                className={cn(
                  'mt-1 truncate font-mono text-ui-label font-bold',
                  stat.color
                )}
              >
                {stat.value}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
        <div className="mb-2 px-2 text-ui-caption font-black uppercase tracking-[0.2em] text-slate-600">
          Runs
        </div>
        <div className="space-y-1">
          {strategyRuns.length === 0 && (
            <div className="rounded-md border border-dashed border-white/10 px-3 py-ui-section text-center text-ui-caption text-slate-500">
              暂无运行实例
            </div>
          )}
          {strategyRuns.map(run => {
            const state = getStrategyRunState(run.mode, run.status);
            const isActive = run.id === displayRun?.id;

            return (
              <button
                key={run.id}
                type="button"
                onClick={() => openRunDashboard(run)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                  isActive
                    ? 'border-red-500/30 bg-red-500/10 text-red-100'
                    : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
                )}
              >
                <span
                  className={cn(
                    'h-2 w-2 shrink-0 rounded-full',
                    state.status === 'RUNNING'
                      ? 'bg-emerald-400'
                      : state.color === 'rose'
                        ? 'bg-rose-400'
                        : 'bg-slate-600'
                  )}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ui-label font-bold">
                    {run.name}
                  </span>
                  <span className="block truncate text-ui-caption text-slate-600">
                    {state.modeLabel} / {state.statusLabel}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );

  const detailContent = (
    <div
      className={cn(
        'studio-workspace-surface h-full min-h-0 p-3',
        activeTab === 'logs'
          ? 'flex flex-col gap-3 overflow-hidden'
          : 'overflow-y-auto custom-scrollbar'
      )}
    >
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className={cn(
          'flex flex-col gap-3',
          activeTab === 'logs' && 'min-h-0 flex-1 overflow-hidden'
        )}
      >
        {/* Unified Command Center Header Card */}
        <div className="relative overflow-hidden border border-white/5 bg-[#0b1120]/70 px-ui-section py-3">
          <div className="relative z-10 flex flex-col gap-3 w-full">
            {/* Slim Top Row: Back Nav & Tags */}
            <div className="flex justify-between items-center w-full">
              <Link
                href="/strategies"
                className="group -ml-2 inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-ui-caption font-bold uppercase tracking-wider text-slate-500 transition-all duration-300 hover:bg-red-500/10 hover:text-red-400"
              >
                <ArrowLeft className="h-3 w-3 transition-transform duration-300 group-hover:-translate-x-0.5" />
                返回策略看板
              </Link>

              <div className="flex flex-wrap items-center justify-end gap-1.5">
                <div
                  className={`px-2 py-0.5 rounded text-ui-micro font-bold uppercase tracking-wider border ${getTagClass(runState?.color)}`}
                >
                  {runState?.statusLabel || '未创建实例'}
                </div>

                {runState && (
                  <div
                    className={cn(
                      'flex items-center gap-1 px-2 py-0.5 rounded text-ui-micro font-bold uppercase tracking-wider border',
                      runState.mode === 'BACKTEST' && getTagClass('red'),
                      runState.mode === 'PAPER' && getTagClass('emerald'),
                      runState.mode === 'LIVE' && getTagClass('rose')
                    )}
                  >
                    {runState.mode === 'BACKTEST' && (
                      <LineChart size={10} strokeWidth={2} />
                    )}
                    {runState.mode === 'PAPER' && (
                      <TestTube size={10} strokeWidth={2} />
                    )}
                    {runState.mode === 'LIVE' && (
                      <Rocket size={10} strokeWidth={2} />
                    )}
                    <span>{runState.modeLabel}</span>
                  </div>
                )}

                {displayRun?.mode === StrategyRunMode.Backtest && (
                  <button
                    type="button"
                    className="flex items-center gap-2 rounded-md border border-red-400/30 bg-red-500/10 px-3 py-1 text-ui-caption font-bold uppercase tracking-wider text-red-200 shadow-sm shadow-red-500/10 hover:border-red-300/60 hover:bg-red-500/20"
                    onClick={() => setActiveTab('history')}
                    aria-label="查看当前回测版本"
                  >
                    <History size={10} strokeWidth={2} />
                    <span className="text-red-300">回测版本</span>
                    <span className="font-mono text-slate-50">
                      {currentBacktestVersion
                        ? `v${currentBacktestVersion.version}`
                        : '-'}
                    </span>
                    {currentBacktestVersion?.status && (
                      <span className="hidden sm:inline text-slate-400">
                        {backtestStatusLabel}
                      </span>
                    )}
                  </button>
                )}
              </div>
            </div>

            {/* Main Row: Identity & Compact Controls */}
            <div className="flex flex-col xl:flex-row justify-between xl:items-center gap-ui-section w-full pb-3">
              {/* Identity compressed */}
              <div className="flex items-center gap-ui-section flex-1 min-w-0 pr-4">
                <div className="w-11 h-11 rounded-panel bg-gradient-to-br from-red-600/20 to-red-500/5 border border-red-500/20 flex items-center justify-center text-red-500 shadow-md shrink-0">
                  <Bot size={22} strokeWidth={1.5} />
                </div>

                <div className="flex flex-col gap-1 min-w-0 flex-1">
                  <h1 className="text-ui-page-title font-bold text-slate-50 tracking-tight truncate">
                    {strategyInstance?.displayName || strategy.name}
                  </h1>
                  <p className="text-slate-400 text-ui-label font-medium truncate max-w-2xl">
                    {strategy.description}
                  </p>
                </div>
              </div>

              {/* Compressed Stats & Actions */}
              <div className="flex items-center gap-ui-section shrink-0 hidden md:flex xl:ml-auto">
                {/* Ultracompact Stats List */}
                <div className="flex items-center gap-ui-section pr-5 border-r border-white/10">
                  {stats.map((stat, i) => (
                    <div key={i} className="flex flex-col items-start gap-0.5">
                      <span className="text-ui-micro font-medium text-slate-400 uppercase tracking-widest pl-0.5">
                        {stat.label}
                      </span>
                      <span
                        className={`text-ui-body font-mono font-bold leading-none ${stat.color}`}
                      >
                        {stat.value}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Tiny Actions */}
                <div className="flex items-center gap-2.5">
                  {primaryAction &&
                    (() => {
                      const PrimaryIcon = getActionIcon(primaryAction.id);
                      return (
                        <Button
                          className={cn(
                            'rounded-lg h-control-compact px-ui-section text-white shadow text-ui-caption font-bold uppercase tracking-wider',
                            getPrimaryActionClass(primaryAction.tone)
                          )}
                          onClick={() => void executeAction(primaryAction.id)}
                        >
                          <PrimaryIcon className="mr-1.5 h-3 w-3" />
                          {primaryAction.label}
                        </Button>
                      );
                    })()}
                  {!primaryAction && !displayRun && (
                    <Button
                      className="rounded-lg h-control-compact px-ui-section bg-red-600 hover:bg-red-500 text-white shadow shadow-red-600/20 text-ui-caption font-bold uppercase tracking-wider"
                      onClick={() => setLocation(`/strategies/${id}/run`)}
                    >
                      <Play className="mr-1.5 h-3 w-3 fill-current" />
                      新建实例
                    </Button>
                  )}
                  {secondaryActions.length > 0 && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button className="h-control-compact px-2 bg-white/5 hover:bg-white/10 text-white rounded-lg focus-visible:ring-0 focus-visible:ring-offset-0">
                          <ChevronDown className="h-3 w-3" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent
                        align="end"
                        className="w-36 border-white/10 bg-[#0F1729] shadow-none p-1 flex flex-col gap-1"
                      >
                        {secondaryActions.map(action => {
                          const ItemIcon = getActionIcon(action.id);
                          return (
                            <DropdownMenuItem
                              key={action.id}
                              className={cn(
                                'text-ui-caption h-8 font-bold uppercase tracking-wider cursor-pointer',
                                action.dangerous
                                  ? 'text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 focus:bg-rose-500/10 focus:text-rose-300'
                                  : 'text-slate-300 hover:text-red-300 hover:bg-red-500/10 focus:bg-red-500/10 focus:text-red-300'
                              )}
                              onClick={() => void executeAction(action.id)}
                            >
                              <ItemIcon className="mr-2 h-3.5 w-3.5" />
                              {action.label}
                            </DropdownMenuItem>
                          );
                        })}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-control-compact w-8 ml-0.5 rounded-lg border-white/10 bg-transparent text-slate-400 hover:text-white hover:bg-white/5"
                    onClick={() => setActiveTab('config')}
                  >
                    <Settings size={14} />
                  </Button>
                </div>
              </div>
            </div>

            {/* Navigation Tabs */}
            <div className="mt-1 border-t border-white/5 pt-2">
              <ScrollableStrategyTabs
                tabs={strategyRunTabs}
                activeTab={activeTab}
              />
            </div>
          </div>
        </div>

        {/* Main Content Area */}
        <div
          className={cn(
            'w-full',
            activeTab === 'logs' && 'min-h-0 flex-1 overflow-hidden'
          )}
        >
          <TabsContent value="monitor" className="mt-0">
            <StrategyMonitor
              activeRun={displayRun}
              instrumentScope={strategy.instrumentScope ?? undefined}
              strategyId={String(strategy.id)}
              backtestRange={backtestMonitorRange}
              backtestId={
                displayRun?.mode === StrategyRunMode.Backtest
                  ? selectedBacktestId
                  : null
              }
              backtestVersion={currentBacktestVersion?.version ?? null}
              decisions={decisionHistory}
              executions={executionTrace}
              className="h-[600px]"
            />
          </TabsContent>

          <TabsContent value="overview" className="mt-0">
            <StrategyOverviewTab
              strategy={strategy}
              instance={strategyInstance}
              backtestRange={backtestMonitorRange}
              activeRun={
                displayRun
                  ? {
                      status: displayRun.status,
                      startTime: displayRun.startTime,
                      instruments: displayRun.instruments,
                    }
                  : null
              }
            />
          </TabsContent>

          {isLimitUpBoardStrategy && (
            <TabsContent value="board" className="mt-0">
              <LimitUpBoardWorkbench
                runId={displayRun?.id}
                instrumentCode={strategyInstance?.instrumentCode}
                runMode={displayRun?.mode}
                runStatus={displayRun?.status}
                parameters={strategyInstance?.parameters}
                decisions={decisionHistory}
                executions={executionTrace}
                backtestId={
                  displayRun?.mode === StrategyRunMode.Backtest
                    ? selectedBacktestId
                    : null
                }
                active={activeTab === 'board'}
              />
            </TabsContent>
          )}

          <TabsContent value="audit" className="mt-0">
            <DecisionAuditTab
              instance={strategyInstance}
              decisions={decisionHistory}
            />
          </TabsContent>

          <TabsContent value="execution" className="mt-0">
            <ExecutionTraceTab
              instance={strategyInstance}
              traces={executionTrace}
            />
          </TabsContent>

          <TabsContent value="buckets" className="mt-0">
            <BucketLedgerTab
              instance={strategyInstance}
              ledger={bucketLedger}
            />
          </TabsContent>

          {isPullbackGridStrategy && (
            <TabsContent value="gridbook" className="mt-0">
              <GridBookTab
                instance={strategyInstance}
                runId={displayRun?.id}
                backtestId={
                  displayRun?.mode === StrategyRunMode.Backtest
                    ? selectedBacktestId
                    : null
                }
              />
            </TabsContent>
          )}

          <TabsContent
            value="logs"
            className={cn(
              'mt-0',
              activeTab === 'logs' && 'h-full min-h-0 overflow-hidden'
            )}
          >
            <StrategyLogsTab
              runId={displayRun?.id}
              strategyName={strategy.name}
              isRunning={runState?.status === 'RUNNING'}
              runMode={displayRun?.mode}
              backtestId={
                displayRun?.mode === StrategyRunMode.Backtest
                  ? selectedBacktestId
                  : null
              }
              backtestVersion={currentBacktestVersion?.version ?? null}
              fillAvailable={activeTab === 'logs'}
              status={displayRun?.status}
            />
          </TabsContent>

          <TabsContent value="performance" className="mt-0">
            <PerformanceTab
              runId={displayRun?.id}
              runMode={displayRun?.mode}
              selectedBacktestId={selectedBacktestId}
              currentBacktestVersion={currentBacktestVersion}
              runStatus={displayRun?.status}
              active={activeTab === 'performance'}
            />
          </TabsContent>

          <TabsContent value="config" className="mt-0">
            <StrategyConfigTab
              strategyId={String(id)}
              strategy={strategy}
              instance={strategyInstance}
              runId={displayRun?.id}
              currentParameters={strategyInstance?.parameters}
              defaultParameters={strategy.defaultParameters}
              runMode={displayRun?.mode}
            />
          </TabsContent>

          {/* 回测版本（仅回测模式可见） */}
          {displayRun?.mode === StrategyRunMode.Backtest && (
            <TabsContent value="history" className="mt-0">
              <BacktestHistoryTab
                runId={displayRun.id}
                mode={displayRun.mode}
                currentBacktestId={selectedBacktestId}
                onTemplateSelect={() => setSelectedBacktestId(null)}
                onVersionSelect={backtest => setSelectedBacktestId(backtest.id)}
                onVersionDeleted={backtestId => {
                  if (selectedBacktestId === backtestId) {
                    setSelectedBacktestId(null);
                  }
                  reexecuteBacktestHistory({ requestPolicy: 'network-only' });
                }}
              />
            </TabsContent>
          )}
        </div>
      </Tabs>
    </div>
  );

  return (
    <>
      <StrategyStudioShell
        activeMode={studioActiveMode}
        className="h-full min-h-0"
        content={detailContent}
        onModeChange={handleStudioModeChange}
        sidebar={detailSidebar}
        statusBarLeft={
          <>
            <span className="inline-flex items-center gap-2">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  runState?.status === 'RUNNING'
                    ? 'bg-emerald-400'
                    : 'bg-slate-500'
                )}
              />
              {runState?.statusLabel || '未创建实例'}
            </span>
            <span className="text-slate-700">|</span>
            <span>{runState?.modeLabel || '策略详情'}</span>
          </>
        }
        statusBarRight={
          <>
            <span>{strategyInstance?.instrumentCode || '未绑定标的'}</span>
            <span className="text-slate-700">|</span>
            <span className="font-mono">{displayRun?.id || '-'}</span>
            {displayRun?.mode === StrategyRunMode.Backtest && (
              <>
                <span className="text-slate-700">|</span>
                <span>
                  {currentBacktestVersion
                    ? `Backtest v${currentBacktestVersion.version}`
                    : 'Backtest'}
                </span>
              </>
            )}
          </>
        }
        tabBar={
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/80 px-3">
            <div className="flex min-w-0 items-center gap-3">
              <Bot className="h-4 w-4 shrink-0 text-red-400" />
              <div className="min-w-0">
                <div className="truncate text-ui-caption font-black uppercase tracking-[0.2em] text-slate-200">
                  {strategyInstance?.displayName || strategy.name}
                </div>
                <div className="truncate text-ui-caption font-medium text-slate-600">
                  {activeTab} / {runState?.modeLabel || 'template'}
                </div>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {primaryAction &&
                (() => {
                  const PrimaryIcon = getActionIcon(primaryAction.id);
                  return (
                    <button
                      type="button"
                      onClick={() => void executeAction(primaryAction.id)}
                      className={cn(
                        'flex h-7 items-center gap-1.5 rounded-md px-2.5 text-ui-caption font-black uppercase tracking-wider text-white transition-colors',
                        getPrimaryActionClass(primaryAction.tone)
                      )}
                    >
                      <PrimaryIcon className="h-3.5 w-3.5" />
                      {primaryAction.label}
                    </button>
                  );
                })()}
              <button
                type="button"
                onClick={() => setActiveTab('config')}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 text-slate-500 transition-colors hover:border-red-500/40 hover:text-red-300"
                title="参数配置"
              >
                <Settings className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        }
      />
      <Dialog
        open={rerunDialogOpen}
        onOpenChange={open => {
          if (!rerunSubmitting) setRerunDialogOpen(open);
        }}
      >
        <DialogContent className="border-slate-800 bg-slate-950 text-slate-100 sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>重新回测</DialogTitle>
            <DialogDescription>
              在当前策略实例下创建新版本，保留已有版本和结果。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-ui-section">
            <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/70 px-ui-section py-3 text-ui-body">
              <span className="text-slate-400">当前范围</span>
              <span className="font-semibold text-slate-100">
                {backtestRangeLabel || '-'}
              </span>
            </div>

            <DateRangePicker
              value={rerunDateRange}
              onChange={range => {
                setRerunDateRange(range);
                setRerunError(null);
              }}
              buttonClassName="min-h-12 border-slate-700 bg-slate-900/80 text-slate-100 hover:border-red-500/70"
            />

            {rerunError && (
              <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-ui-section py-3 text-ui-body text-rose-200">
                {rerunError}
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={rerunSubmitting}
              onClick={() => setRerunDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={
                rerunSubmitting || !rerunDateRange?.from || !rerunDateRange?.to
              }
              onClick={createBacktestVersion}
            >
              {rerunSubmitting ? '创建中...' : '创建新版本'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={confirmDialog.open}
        onOpenChange={open => {
          if (!open) {
            paperSnapshotDraftRef.current = null;
          }
          setConfirmDialog(prev => ({ ...prev, open }));
        }}
        title={confirmDialog.title}
        description={
          confirmDialog.paperSnapshotDraft ? (
            <PaperAccountSnapshotEditor
              draft={confirmDialog.paperSnapshotDraft}
              onChange={updatePaperSnapshotDraft}
            />
          ) : (
            confirmDialog.description
          )
        }
        confirmText={confirmDialog.confirmText || '确认'}
        loadingText="处理中..."
        cancelText="取消"
        variant={confirmDialog.variant}
        onConfirm={confirmDialog.onConfirm}
      />
    </>
  );
}

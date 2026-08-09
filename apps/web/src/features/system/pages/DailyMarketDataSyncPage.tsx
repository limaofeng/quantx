import {
  ArrowLeft,
  AlertTriangle,
  CalendarDays,
  CandlestickChart,
  CheckCircle2,
  Database,
  Info,
  ListFilter,
  SlidersHorizontal,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useStockScreenSnapshotStatus } from '@/features/screening/hooks/useStockScreenSnapshotStatus';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentRunMonitor } from '../components/DeploymentRunMonitor';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';

import { validateMarketDataSync } from './marketDataSyncValidation';

const sectorOptions = ['沪深A股', '沪深ETF', '沪深指数'];
const periodOptions = [
  { label: '日线', value: '1d', description: '1d K线，指标快照依赖' },
  { label: '分钟', value: '1m', description: '1m K线，盘后增量缓存' },
  { label: 'Tick', value: 'tick', description: '逐笔/盘口明细，数据量大' },
] as const;

type PeriodValue = (typeof periodOptions)[number]['value'];
type TargetMode = 'sectors' | 'stocks';

function todayInputValue() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function toCompactDate(value: string) {
  return value ? value.replace(/-/g, '') : '';
}

function parseList(value: string) {
  return value
    .split(/[\s,，;；]+/)
    .map(item => item.trim().toUpperCase())
    .filter(Boolean);
}

function formatDeploymentTime(value: string | null | undefined) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
  });
}

interface MetricTileProps {
  icon: React.ElementType;
  label: string;
  value: string;
}

function MetricTile({ icon: Icon, label, value }: MetricTileProps) {
  return (
    <div className="rounded-xl border border-slate-200/60 bg-white/70 p-4 shadow-sm dark:border-white/5 dark:bg-white/[0.03]">
      <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-2 text-lg font-black text-slate-900 dark:text-white">
        {value}
      </div>
    </div>
  );
}

export function DailyMarketDataSyncPage() {
  const [, setLocation] = useLocation();
  const sync = useDeploymentSync('daily-market-data-sync', {
    successMessage: 'K线批量同步任务已提交',
  });
  const snapshotStatus = useStockScreenSnapshotStatus();
  const [targetMode, setTargetMode] = useState<TargetMode>('sectors');
  const [selectedSectors, setSelectedSectors] =
    useState<string[]>(sectorOptions);
  const [stockText, setStockText] = useState('000001.SZ\n600000.SH');
  const [startDate, setStartDate] = useState(todayInputValue());
  const [endDate, setEndDate] = useState(todayInputValue());
  const [periods, setPeriods] = useState<PeriodValue[]>(['1d', '1m']);
  const [skipDownload, setSkipDownload] = useState(false);
  const [computeDailySignals, setComputeDailySignals] = useState(true);

  const stockList = useMemo(() => parseList(stockText), [stockText]);
  const validationMessage = useMemo(
    () =>
      validateMarketDataSync({
        startDate,
        endDate,
        targetMode,
        stockCount: stockList.length,
        periods,
        skipDownload,
        computeDailySignals,
      }),
    [
      computeDailySignals,
      endDate,
      periods,
      skipDownload,
      startDate,
      stockList.length,
      targetMode,
    ]
  );
  const syncParameters = useMemo(() => {
    const parameters: Record<string, unknown> = {
      compute_daily_signals: computeDailySignals,
      end_time: toCompactDate(endDate),
      periods,
      skip_download: skipDownload,
      start_time: toCompactDate(startDate),
    };

    if (targetMode === 'sectors') {
      parameters.sectors = selectedSectors;
    } else {
      parameters.stock_list = stockList;
    }

    return parameters;
  }, [
    computeDailySignals,
    endDate,
    periods,
    selectedSectors,
    skipDownload,
    startDate,
    stockList,
    targetMode,
  ]);

  const toggleSector = (sector: string) => {
    setSelectedSectors(current => {
      if (current.includes(sector)) {
        return current.length === 1
          ? current
          : current.filter(item => item !== sector);
      }
      return [...current, sector];
    });
  };

  const togglePeriod = (period: PeriodValue) => {
    setPeriods(current => {
      if (current.includes(period)) {
        return current.length === 1
          ? current
          : current.filter(item => item !== period);
      }
      return [...current, period];
    });
  };

  return (
    <DataStudioPageFrame
      activeMode="MARKET_DATA"
      description="每日市场行情 K线/tick 数据同步"
      title="K线批量同步"
    >
      <div className="flex flex-col gap-4 pb-8 animate-fade-in">
        <div className="flex flex-col gap-3 py-1 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 rounded-lg border border-slate-200/60 bg-white/50 shadow-sm backdrop-blur-sm transition-colors hover:bg-white dark:border-white/5 dark:bg-white/5 dark:hover:bg-white/10"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="h-4 w-4 text-slate-600 dark:text-slate-400" />
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-black leading-none tracking-tight text-slate-900 dark:text-white">
                K线批量同步
              </h1>
              <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">
                daily-market-data-sync · sectors / stock_list / date range
              </p>
            </div>
          </div>

          <DeploymentSyncControl
            sync={sync}
            defaultFlowName="每日市场行情同步"
            historyFallbackName="daily-market-data-sync"
            syncParameters={syncParameters}
            syncDisabled={Boolean(validationMessage)}
            syncDisabledReason={validationMessage || undefined}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <MetricTile
            icon={CheckCircle2}
            label="最新快照"
            value={snapshotStatus.status?.latestSnapshotDate || '尚无快照'}
          />
          <MetricTile
            icon={CalendarDays}
            label="应有快照"
            value={snapshotStatus.status?.expectedSnapshotDate || '--'}
          />
          <MetricTile
            icon={AlertTriangle}
            label="缺失交易日"
            value={`${snapshotStatus.status?.missingSnapshotDates.length ?? 0} 日`}
          />
          <MetricTile
            icon={CalendarDays}
            label="自动调度"
            value={
              sync.deployment?.isScheduleActive ? '15:05 工作日' : '已暂停'
            }
          />
          <MetricTile
            icon={Database}
            label="下次运行"
            value={formatDeploymentTime(sync.deployment?.nextRunTime)}
          />
          <MetricTile
            icon={CandlestickChart}
            label="当前周期"
            value={periods.join(' + ')}
          />
        </div>

        {snapshotStatus.error ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs font-semibold text-rose-700 dark:text-rose-300"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>快照状态查询失败：{snapshotStatus.error.message}</span>
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-4">
            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-black text-slate-900 dark:text-white">
                    同步目标
                  </h2>
                  <p className="mt-1 text-xs font-medium text-slate-500">
                    按板块展开全量标的，或手动输入明确股票代码。
                  </p>
                </div>
                <div className="flex rounded-lg border border-slate-200/60 bg-slate-50 p-1 dark:border-white/5 dark:bg-white/[0.03]">
                  {[
                    { label: '板块', value: 'sectors' },
                    { label: '标的', value: 'stocks' },
                  ].map(option => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={targetMode === option.value}
                      className={cn(
                        'h-7 rounded-md px-3 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                        targetMode === option.value
                          ? 'bg-white text-blue-700 shadow-sm dark:bg-slate-800 dark:text-blue-300'
                          : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                      )}
                      onClick={() => setTargetMode(option.value as TargetMode)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              {targetMode === 'sectors' ? (
                <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                  {sectorOptions.map(sector => {
                    const checked = selectedSectors.includes(sector);
                    return (
                      <button
                        key={sector}
                        type="button"
                        aria-pressed={checked}
                        className={cn(
                          'flex min-h-[88px] flex-col justify-between rounded-xl border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                          checked
                            ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                            : 'border-slate-200/60 bg-slate-50/80 text-slate-600 hover:bg-white dark:border-white/5 dark:bg-white/[0.02] dark:text-slate-400 dark:hover:bg-white/[0.04]'
                        )}
                        onClick={() => toggleSector(sector)}
                      >
                        <span className="text-sm font-black">{sector}</span>
                        <span className="mt-2 inline-flex items-center gap-1.5 text-[10px] font-bold">
                          {checked && <CheckCircle2 className="h-3 w-3" />}
                          {checked ? '将参与同步' : '点击加入范围'}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-4">
                  <label
                    htmlFor="market-data-stock-list"
                    className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500"
                  >
                    Stock List
                  </label>
                  <Textarea
                    id="market-data-stock-list"
                    value={stockText}
                    onChange={event => setStockText(event.target.value)}
                    className="mt-2 min-h-[132px] resize-y border-slate-200/70 bg-slate-50/70 font-mono text-xs dark:border-white/10 dark:bg-white/[0.03]"
                    placeholder="000001.SZ, 600000.SH"
                  />
                  <div className="mt-2 flex items-center justify-between text-[10px] font-bold text-slate-500">
                    <span>支持逗号、空格或换行分隔。</span>
                    <span>{stockList.length} 只标的</span>
                  </div>
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center gap-2">
                <CalendarDays className="h-4 w-4 text-blue-500" />
                <h2 className="text-sm font-black text-slate-900 dark:text-white">
                  日期与周期
                </h2>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label
                    htmlFor="market-sync-start"
                    className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500"
                  >
                    Start Date
                  </label>
                  <Input
                    id="market-sync-start"
                    type="date"
                    value={startDate}
                    onChange={event => setStartDate(event.target.value)}
                    className="mt-2 h-9 border-slate-200/70 bg-slate-50/70 text-xs font-bold dark:border-white/10 dark:bg-white/[0.03]"
                  />
                </div>
                <div>
                  <label
                    htmlFor="market-sync-end"
                    className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500"
                  >
                    End Date
                  </label>
                  <Input
                    id="market-sync-end"
                    type="date"
                    value={endDate}
                    onChange={event => setEndDate(event.target.value)}
                    className="mt-2 h-9 border-slate-200/70 bg-slate-50/70 text-xs font-bold dark:border-white/10 dark:bg-white/[0.03]"
                  />
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {periodOptions.map(option => {
                  const checked = periods.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={checked}
                      className={cn(
                        'min-w-[126px] rounded-lg border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                        checked
                          ? 'border-blue-500/30 bg-blue-500/10'
                          : 'border-slate-200/70 bg-slate-50/70 hover:bg-white dark:border-white/10 dark:bg-white/[0.02] dark:hover:bg-white/[0.04]'
                      )}
                      onClick={() => togglePeriod(option.value)}
                    >
                      <div className="text-xs font-black text-slate-900 dark:text-white">
                        {option.label}
                        <span className="ml-1 font-mono text-[10px] text-slate-500">
                          {option.value}
                        </span>
                      </div>
                      <div className="mt-1 text-[10px] font-medium leading-relaxed text-slate-500">
                        {option.description}
                      </div>
                    </button>
                  );
                })}
              </div>

              <div className="mt-4 rounded-lg border border-blue-500/15 bg-blue-500/5 p-3 text-xs font-medium leading-relaxed text-blue-700 dark:text-blue-300">
                <Info className="mr-2 inline h-3.5 w-3.5" />
                手动运行会提交明确的 start_time /
                end_time；清空日期时，后端会使用 Prefect 计划时间解析目标日期。
              </div>
            </section>

            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-blue-500" />
                <h2 className="text-sm font-black text-slate-900 dark:text-white">
                  执行选项
                </h2>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                <label
                  htmlFor="market-sync-skip-download"
                  className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200/60 bg-slate-50/70 p-3 dark:border-white/5 dark:bg-white/[0.02]"
                >
                  <span>
                    <span className="block text-xs font-black text-slate-800 dark:text-slate-100">
                      仅补算指标（使用已入库 1d K线）
                    </span>
                    <span className="mt-1 block text-[10px] font-medium text-slate-500">
                      不请求 QMT Agent，只读取 InfluxDB 中已有日线。
                    </span>
                  </span>
                  <Switch
                    id="market-sync-skip-download"
                    checked={skipDownload}
                    onCheckedChange={checked => {
                      setSkipDownload(checked);
                      if (checked) {
                        setComputeDailySignals(true);
                        setPeriods(current =>
                          current.includes('1d') ? current : ['1d', ...current]
                        );
                      }
                    }}
                  />
                </label>

                <label
                  htmlFor="market-sync-signals"
                  className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200/60 bg-slate-50/70 p-3 dark:border-white/5 dark:bg-white/[0.02]"
                >
                  <span>
                    <span className="block text-xs font-black text-slate-800 dark:text-slate-100">
                      计算日级指标
                    </span>
                    <span className="mt-1 block text-[10px] font-medium text-slate-500">
                      日线同步完成后触发快照预计算。
                    </span>
                  </span>
                  <Switch
                    id="market-sync-signals"
                    checked={computeDailySignals}
                    onCheckedChange={checked => {
                      setComputeDailySignals(checked);
                      if (!checked) setSkipDownload(false);
                    }}
                  />
                </label>
              </div>
              {validationMessage && (
                <div
                  className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3 text-xs font-bold leading-relaxed text-amber-700 dark:text-amber-300"
                  role="alert"
                  aria-live="polite"
                >
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  {validationMessage}
                </div>
              )}
            </section>
          </div>

          <div className="space-y-4">
            <DeploymentRunMonitor
              deploymentId={sync.deployment?.id}
              deploymentName="daily-market-data-sync"
            />

            <section className="rounded-xl border border-slate-200/60 bg-slate-950 p-4 text-slate-200 shadow-sm dark:border-white/5">
              <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">
                <ListFilter className="h-3.5 w-3.5" />
                Parameters Preview
              </div>
              <pre className="mt-3 max-h-[260px] overflow-auto rounded-lg bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300 custom-scrollbar">
                {JSON.stringify(syncParameters, null, 2)}
              </pre>
            </section>
          </div>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

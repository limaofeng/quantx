import {
  ArrowLeft,
  BadgeCheck,
  FileSearch,
  Hash,
  Info,
  Megaphone,
  RefreshCw,
  ShieldCheck,
  Users,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentRunMonitor } from '../components/DeploymentRunMonitor';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';

type AnnouncementTargetMode = 'auto' | 'manual';

function parseStockCodes(value: string) {
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

interface InfoTileProps {
  icon: React.ElementType;
  label: string;
  value: string;
}

function InfoTile({ icon: Icon, label, value }: InfoTileProps) {
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

export function AnnouncementSyncPage() {
  const [, setLocation] = useLocation();
  const sync = useDeploymentSync('announcement-sync', {
    successMessage: '公告与回购同步任务已提交',
  });
  const [targetMode, setTargetMode] = useState<AnnouncementTargetMode>('auto');
  const [stockText, setStockText] = useState('600519.SH\n300750.SZ');
  const [limit, setLimit] = useState('');
  const [force, setForce] = useState(false);

  const stockCodes = useMemo(() => parseStockCodes(stockText), [stockText]);
  const syncParameters = useMemo(() => {
    const parameters: Record<string, unknown> = { force };
    const parsedLimit = Number(limit);

    if (targetMode === 'manual') {
      parameters.stock_codes = stockCodes;
    }

    if (Number.isFinite(parsedLimit) && parsedLimit > 0) {
      parameters.limit = Math.floor(parsedLimit);
    }

    return parameters;
  }, [force, limit, stockCodes, targetMode]);

  return (
    <DataStudioPageFrame
      activeMode="ANNOUNCEMENTS"
      description="上市公司公告与回购事件同步"
      title="公告同步"
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
                公告与回购同步
              </h1>
              <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">
                announcement-sync · holdings / watchlist / stock_codes
              </p>
            </div>
          </div>

          <DeploymentSyncControl
            sync={sync}
            defaultFlowName="上市公司公告与回购同步"
            historyFallbackName="announcement-sync"
            syncParameters={syncParameters}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <InfoTile
            icon={Megaphone}
            label="自动调度"
            value={
              sync.deployment?.isScheduleActive ? '15:45 工作日' : '已暂停'
            }
          />
          <InfoTile
            icon={FileSearch}
            label="下次运行"
            value={formatDeploymentTime(sync.deployment?.nextRunTime)}
          />
          <InfoTile icon={Users} label="默认范围" value="持仓 / 自选" />
          <InfoTile
            icon={BadgeCheck}
            label="刷新模式"
            value={force ? '强制刷新' : '增量同步'}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-4">
            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-black text-slate-900 dark:text-white">
                    同步范围
                  </h2>
                  <p className="mt-1 text-xs font-medium text-slate-500">
                    默认从持仓股和自选股收集标的，也可以指定股票列表做临时刷新。
                  </p>
                </div>
                <div className="flex rounded-lg border border-slate-200/60 bg-slate-50 p-1 dark:border-white/5 dark:bg-white/[0.03]">
                  {[
                    { label: '自动收集', value: 'auto' },
                    { label: '指定标的', value: 'manual' },
                  ].map(option => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={targetMode === option.value}
                      className={cn(
                        'h-7 rounded-md px-3 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/60',
                        targetMode === option.value
                          ? 'bg-white text-violet-700 shadow-sm dark:bg-slate-800 dark:text-violet-300'
                          : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                      )}
                      onClick={() =>
                        setTargetMode(option.value as AnnouncementTargetMode)
                      }
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              {targetMode === 'auto' ? (
                <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div className="rounded-xl border border-violet-500/20 bg-violet-500/10 p-4">
                    <div className="flex items-center gap-2 text-sm font-black text-violet-700 dark:text-violet-300">
                      <Users className="h-4 w-4" />
                      持仓股与自选股
                    </div>
                    <p className="mt-2 text-xs font-medium leading-relaxed text-slate-600 dark:text-slate-400">
                      Flow 会调用
                      collect_disclosure_sync_symbols，自动合并需要跟踪的标的集合。
                    </p>
                  </div>
                  <div className="rounded-xl border border-slate-200/60 bg-slate-50/80 p-4 dark:border-white/5 dark:bg-white/[0.02]">
                    <div className="flex items-center gap-2 text-sm font-black text-slate-800 dark:text-slate-100">
                      <Hash className="h-4 w-4 text-slate-500" />
                      Limit 保护
                    </div>
                    <p className="mt-2 text-xs font-medium leading-relaxed text-slate-600 dark:text-slate-400">
                      可选限制本次处理标的数量，适合排查 provider 或逐步补数据。
                    </p>
                  </div>
                </div>
              ) : (
                <div className="mt-4">
                  <label
                    htmlFor="announcement-stock-list"
                    className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500"
                  >
                    Stock Codes
                  </label>
                  <Textarea
                    id="announcement-stock-list"
                    value={stockText}
                    onChange={event => setStockText(event.target.value)}
                    className="mt-2 min-h-[132px] resize-y border-slate-200/70 bg-slate-50/70 font-mono text-xs dark:border-white/10 dark:bg-white/[0.03]"
                    placeholder="600519.SH, 300750.SZ"
                  />
                  <div className="mt-2 flex items-center justify-between text-[10px] font-bold text-slate-500">
                    <span>支持逗号、空格或换行分隔。</span>
                    <span>{stockCodes.length} 只标的</span>
                  </div>
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center gap-2">
                <RefreshCw className="h-4 w-4 text-violet-500" />
                <h2 className="text-sm font-black text-slate-900 dark:text-white">
                  执行参数
                </h2>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label
                    htmlFor="announcement-limit"
                    className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500"
                  >
                    Limit
                  </label>
                  <Input
                    id="announcement-limit"
                    type="number"
                    min={1}
                    value={limit}
                    onChange={event => setLimit(event.target.value)}
                    className="mt-2 h-9 border-slate-200/70 bg-slate-50/70 text-xs font-bold dark:border-white/10 dark:bg-white/[0.03]"
                    placeholder="不限制"
                  />
                </div>

                <label
                  htmlFor="announcement-force"
                  className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200/60 bg-slate-50/70 p-3 dark:border-white/5 dark:bg-white/[0.02]"
                >
                  <span>
                    <span className="block text-xs font-black text-slate-800 dark:text-slate-100">
                      强制刷新
                    </span>
                    <span className="mt-1 block text-[10px] font-medium text-slate-500">
                      忽略已有缓存，重新拉取公告与回购事件。
                    </span>
                  </span>
                  <Switch
                    id="announcement-force"
                    checked={force}
                    onCheckedChange={setForce}
                  />
                </label>
              </div>

              <div className="mt-4 rounded-lg border border-violet-500/15 bg-violet-500/5 p-3 text-xs font-medium leading-relaxed text-violet-700 dark:text-violet-300">
                <Info className="mr-2 inline h-3.5 w-3.5" />
                空参数会按默认持仓/自选股增量同步；指定 stock_codes
                时只处理输入列表。
              </div>
            </section>

            <section className="rounded-xl border border-slate-200/60 bg-white/70 p-5 shadow-sm backdrop-blur-sm dark:border-white/5 dark:bg-white/[0.03]">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-violet-500" />
                <h2 className="text-sm font-black text-slate-900 dark:text-white">
                  处理链路
                </h2>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                {[
                  ['1', '收集标的', '持仓、自选或指定列表'],
                  ['2', '同步公告', '上市公司公告与 PDF 链接'],
                  ['3', '识别回购', '回购进展与金额区间'],
                ].map(([step, title, description]) => (
                  <div
                    key={step}
                    className="rounded-lg border border-slate-200/60 bg-slate-50/70 p-3 dark:border-white/5 dark:bg-white/[0.02]"
                  >
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded bg-violet-500/10 font-mono text-[10px] font-black text-violet-600">
                        {step}
                      </span>
                      <span className="text-xs font-black text-slate-900 dark:text-white">
                        {title}
                      </span>
                    </div>
                    <p className="mt-2 text-[10px] font-medium leading-relaxed text-slate-500">
                      {description}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="space-y-4">
            <DeploymentRunMonitor
              deploymentId={sync.deployment?.id}
              deploymentName="announcement-sync"
            />

            <section className="rounded-xl border border-slate-200/60 bg-slate-950 p-4 text-slate-200 shadow-sm dark:border-white/5">
              <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">
                <FileSearch className="h-3.5 w-3.5" />
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

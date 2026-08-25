import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  Loader2,
  Play,
  type LucideIcon,
  Radio,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Square,
  Target,
  WalletCards,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/utils/cn';

import type {
  LimitUpBoardHealthItemTone,
  LimitUpBoardHealthSummary,
} from '../domain/limitUpBoardHealth';

const ITEM_ICONS: Record<string, LucideIcon> = {
  assistant: Bot,
  coverage: Target,
  'entry-gate': ShieldAlert,
  exits: WalletCards,
  projection: DatabaseZap,
  radar: Radio,
};

const TONE_STYLES: Record<
  LimitUpBoardHealthItemTone,
  { dot: string; icon: string; surface: string; text: string }
> = {
  error: {
    dot: 'bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.55)]',
    icon: 'text-rose-300',
    surface: 'border-rose-400/15 bg-rose-400/[0.055] text-rose-200',
    text: 'text-rose-200',
  },
  healthy: {
    dot: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.45)]',
    icon: 'text-emerald-300',
    surface: 'border-emerald-400/15 bg-emerald-400/[0.055] text-emerald-200',
    text: 'text-emerald-200',
  },
  neutral: {
    dot: 'bg-slate-600',
    icon: 'text-slate-500',
    surface: 'border-white/[0.07] bg-white/[0.025] text-slate-300',
    text: 'text-slate-300',
  },
  warning: {
    dot: 'bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.4)]',
    icon: 'text-amber-300',
    surface: 'border-amber-400/15 bg-amber-400/[0.055] text-amber-200',
    text: 'text-amber-200',
  },
};

function formatTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LimitUpBoardHealthConsole({
  accountId,
  accountName,
  actionLoading,
  assistantEnabled,
  health,
  mode,
  onOpenSettings,
  onRefresh,
  onToggleAssistant,
  radarUpdatedAt,
  refreshing,
}: {
  accountId?: string;
  accountName?: string;
  actionLoading: boolean;
  assistantEnabled: boolean;
  health: LimitUpBoardHealthSummary;
  mode: string;
  onOpenSettings: () => void;
  onRefresh: () => void;
  onToggleAssistant: (enabled: boolean) => void;
  radarUpdatedAt?: string | null;
  refreshing: boolean;
}) {
  const overallLabel =
    health.tone === 'error'
      ? '需要处置'
      : health.tone === 'warning'
        ? '需要关注'
        : '业务链健康';
  const primaryIssue =
    health.items.find(item => item.tone === 'error') ??
    health.items.find(item => item.tone === 'warning') ??
    null;
  const assistantHealth = health.items.find(item => item.id === 'assistant');
  const entryGateHealth = health.items.find(item => item.id === 'entry-gate');

  return (
    <aside
      aria-labelledby="limit-up-health-console-title"
      className="studio-workspace-surface flex h-full min-h-0 w-full flex-col text-slate-200"
      data-testid="limit-up-health-console"
    >
      <div className="shrink-0 border-b border-white/[0.06] px-ui-section py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-ui-micro font-black uppercase tracking-[0.24em] text-red-300">
              First board operations
            </div>
            <h1
              id="limit-up-health-console-title"
              className="mt-1 text-ui-title font-black"
            >
              健康控制台
            </h1>
            <div className="mt-1 truncate font-mono text-ui-micro text-slate-600">
              {accountName || '未选择账户'} · {accountId || '--'}
            </div>
          </div>
          <button
            type="button"
            aria-label="刷新健康控制台"
            className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-sm border border-white/[0.08] text-slate-500 hover:border-red-400/25 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60 disabled:opacity-40"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw
              className={cn(
                'h-4 w-4',
                refreshing && 'animate-spin motion-reduce:animate-none'
              )}
            />
          </button>
        </div>
      </div>

      <div
        className="grid shrink-0 grid-cols-2 gap-2 border-b border-white/[0.06] p-3"
        role="group"
        aria-label="首板运行摘要"
      >
        <HealthStatusCell
          icon={health.tone === 'healthy' ? CheckCircle2 : ShieldAlert}
          label="业务链"
          tone={health.tone}
          value={overallLabel}
        />
        <HealthStatusCell
          icon={Bot}
          label="晋级助手"
          tone={assistantHealth?.tone ?? 'neutral'}
          value={assistantHealth?.value ?? '等待状态'}
        />
        <HealthStatusCell
          icon={Activity}
          label="执行环境"
          tone={mode === 'live' ? 'warning' : 'neutral'}
          value={mode === 'live' ? 'LIVE 实盘' : 'PAPER 模拟'}
        />
        <HealthStatusCell
          icon={ShieldAlert}
          label="确认门禁"
          tone={entryGateHealth?.tone ?? 'neutral'}
          value={entryGateHealth?.value ?? '等待状态'}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 flex items-center justify-between text-ui-micro font-black uppercase tracking-[0.12em] text-slate-600">
            <span>业务链检查</span>
            <span>更新 {formatTime(radarUpdatedAt)}</span>
          </div>
          <div
            className="border border-white/[0.055] bg-white/[0.018]"
            role="list"
            aria-label="首板业务链检查项"
          >
            {health.items.map(item => {
              const Icon = ITEM_ICONS[item.id] || Activity;
              const style = TONE_STYLES[item.tone];
              return (
                <div
                  key={item.id}
                  className="border-b border-white/[0.055] px-2.5 py-2.5 last:border-b-0"
                  role="listitem"
                >
                  <div className="flex items-center gap-2">
                    <Icon className={cn('h-3.5 w-3.5 shrink-0', style.icon)} />
                    <span className="min-w-0 flex-1 text-ui-caption text-slate-400">
                      {item.label}
                    </span>
                    <span
                      className={cn(
                        'h-1.5 w-1.5 shrink-0 rounded-full',
                        style.dot
                      )}
                    />
                    <strong className={cn('text-ui-caption', style.text)}>
                      {item.value}
                    </strong>
                  </div>
                  <p className="mt-1 pl-[22px] text-ui-micro leading-4 text-slate-600">
                    {item.detail}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        <section className="p-3">
          <div className="mb-2 flex items-center gap-2 text-ui-micro font-black uppercase tracking-[0.12em] text-slate-600">
            {primaryIssue ? (
              <ShieldAlert className="h-3.5 w-3.5 text-amber-300" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
            )}
            首要门禁
          </div>
          <p
            className={cn(
              'text-ui-caption leading-4',
              primaryIssue ? 'text-amber-100' : 'text-emerald-200'
            )}
          >
            {primaryIssue
              ? `${primaryIssue.label} · ${primaryIssue.detail}`
              : '当前没有业务阻断项'}
          </p>
          <p className="mt-3 border-t border-white/[0.05] pt-3 text-ui-micro leading-4 text-slate-600">
            <Clock3 className="mr-1 inline h-3 w-3 text-cyan-400" />
            这里只判断首板扫描、候选收敛、入场门禁与 T+1
            退出计划。Engine、Agent、备份与死信等全局状态仍以底部状态栏和统一设置为准。
          </p>
        </section>
      </div>

      <div className="shrink-0 space-y-2 border-t border-white/[0.06] bg-[#07111f] p-3">
        <div className="grid grid-cols-2 gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-control-compact cursor-pointer rounded-sm border-white/10 bg-transparent text-ui-caption text-slate-300 hover:bg-white/[0.05]"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            同步业务链
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-control-compact cursor-pointer rounded-sm border-white/10 bg-transparent text-ui-caption text-slate-300 hover:bg-white/[0.05]"
            onClick={onOpenSettings}
          >
            <Settings2 className="mr-1.5 h-3.5 w-3.5" />
            风险设置
          </Button>
        </div>
        <Button
          type="button"
          className={cn(
            'h-control-default w-full cursor-pointer rounded-sm text-ui-caption font-black',
            assistantEnabled
              ? 'bg-slate-700 text-white hover:bg-slate-600'
              : 'bg-red-500 text-white hover:bg-red-400'
          )}
          disabled={!accountId || actionLoading}
          onClick={() => onToggleAssistant(!assistantEnabled)}
        >
          {actionLoading ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          ) : assistantEnabled ? (
            <Square className="mr-1.5 h-3 w-3" />
          ) : (
            <Play className="mr-1.5 h-3.5 w-3.5" />
          )}
          {assistantEnabled ? '停止助手' : '启动助手'}
        </Button>
      </div>
    </aside>
  );
}

function HealthStatusCell({
  icon: Icon,
  label,
  tone,
  value,
}: {
  icon: LucideIcon;
  label: string;
  tone: LimitUpBoardHealthItemTone;
  value: string;
}) {
  const style = TONE_STYLES[tone];
  return (
    <div
      className={cn('min-w-0 border px-2.5 py-2', style.surface)}
      role="group"
      aria-label={label}
    >
      <div className="flex items-center gap-1.5 text-ui-micro font-black uppercase tracking-[0.1em] opacity-65">
        <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 truncate text-ui-caption font-black">{value}</div>
    </div>
  );
}

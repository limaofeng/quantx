import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  type LucideIcon,
  Radio,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Target,
  WalletCards,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
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
  { dot: string; icon: string; text: string }
> = {
  error: {
    dot: 'bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.55)]',
    icon: 'text-rose-300',
    text: 'text-rose-200',
  },
  healthy: {
    dot: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.45)]',
    icon: 'text-emerald-300',
    text: 'text-emerald-200',
  },
  neutral: {
    dot: 'bg-slate-600',
    icon: 'text-slate-500',
    text: 'text-slate-300',
  },
  warning: {
    dot: 'bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.4)]',
    icon: 'text-amber-300',
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

  return (
    <aside
      aria-label="首板健康控制台"
      className="flex h-full min-h-0 w-full flex-col bg-[#081423] text-slate-200"
      data-testid="limit-up-health-console"
    >
      <div className="shrink-0 border-b border-white/[0.06] py-3.5 pl-4 pr-14">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[9px] font-black uppercase tracking-[0.24em] text-red-300">
              First board operations
            </div>
            <h1 className="mt-1 text-base font-black">首板健康控制台</h1>
            <div className="mt-1 truncate font-mono text-[9px] text-slate-600">
              {accountName || '未选择账户'} · {accountId || '--'}
            </div>
          </div>
          <button
            type="button"
            aria-label="刷新首板健康控制台"
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

      <div className="grid shrink-0 grid-cols-2 gap-2 border-b border-white/[0.06] p-3">
        <HealthStatusCell
          icon={health.tone === 'healthy' ? CheckCircle2 : ShieldAlert}
          label="首板业务链"
          tone={health.tone}
          value={overallLabel}
        />
        <HealthStatusCell
          icon={Activity}
          label="执行环境"
          tone={mode === 'live' ? 'warning' : 'neutral'}
          value={mode === 'live' ? 'LIVE 实盘' : 'PAPER 模拟'}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 flex items-center justify-between text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            <span>业务链检查</span>
            <span>更新 {formatTime(radarUpdatedAt)}</span>
          </div>
          <div className="space-y-1.5">
            {health.items.map(item => {
              const Icon = ITEM_ICONS[item.id] || Activity;
              const style = TONE_STYLES[item.tone];
              return (
                <div
                  key={item.id}
                  className="rounded-sm border border-white/[0.055] bg-white/[0.018] px-2.5 py-2"
                >
                  <div className="flex items-center gap-2">
                    <Icon className={cn('h-3.5 w-3.5 shrink-0', style.icon)} />
                    <span className="min-w-0 flex-1 text-[10px] text-slate-400">
                      {item.label}
                    </span>
                    <span
                      className={cn(
                        'h-1.5 w-1.5 shrink-0 rounded-full',
                        style.dot
                      )}
                    />
                    <strong className={cn('text-[10px]', style.text)}>
                      {item.value}
                    </strong>
                  </div>
                  <p className="mt-1 pl-[22px] text-[9px] leading-4 text-slate-600">
                    {item.detail}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        <section className="p-3">
          <div className="mb-2 flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            <Clock3 className="h-3.5 w-3.5 text-cyan-400" />
            健康边界
          </div>
          <p className="text-[9px] leading-4 text-slate-600">
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
            className="h-8 cursor-pointer rounded-sm border-white/10 bg-transparent text-[10px] text-slate-300 hover:bg-white/[0.05]"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            同步业务链
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 cursor-pointer rounded-sm border-white/10 bg-transparent text-[10px] text-slate-300 hover:bg-white/[0.05]"
            onClick={onOpenSettings}
          >
            <Settings2 className="mr-1.5 h-3.5 w-3.5" />
            风险设置
          </Button>
        </div>
        <label className="flex h-9 cursor-pointer items-center justify-between rounded-sm border border-white/[0.08] bg-white/[0.025] px-3 text-[10px] text-slate-300">
          <span className="inline-flex items-center gap-2">
            <Bot
              className={cn(
                'h-3.5 w-3.5',
                assistantEnabled ? 'text-emerald-300' : 'text-slate-600'
              )}
            />
            首板晋级助手
          </span>
          <Switch
            aria-label="启用首板晋级助手"
            checked={assistantEnabled}
            disabled={!accountId || actionLoading}
            onCheckedChange={onToggleAssistant}
            className="scale-75 data-[state=checked]:bg-emerald-500"
          />
        </label>
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
    <div className="min-w-0 rounded-sm border border-white/[0.06] bg-white/[0.025] px-2.5 py-2">
      <div className="flex items-center gap-1.5 text-[9px] text-slate-600">
        <Icon className={cn('h-3 w-3', style.icon)} />
        {label}
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        <span className={cn('h-1.5 w-1.5 rounded-full', style.dot)} />
        <strong className={cn('truncate text-[10px]', style.text)}>
          {value}
        </strong>
      </div>
    </div>
  );
}

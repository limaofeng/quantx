import { Copy, Eye, LineChart, Rocket, TestTube, Trash2 } from 'lucide-react';
import { useLocation } from 'wouter';

// Badge removed

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  type StrategyRunStatus,
  type StrategyRunMode,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { getStrategyRunState, type StrategyInstance } from '../domain';

interface StrategyInstanceCardProps {
  run: {
    id: string;
    name: string;
    strategy: {
      id: number;
      name: string;
    };
    instruments: string[];
    mode: StrategyRunMode;
    status: StrategyRunStatus;
    profitLoss: number;
    totalTrades: number;
    startTime?: string | null;
  };
  instance: StrategyInstance;
  onDelete: (runId: string) => void;
}

export default function StrategyInstanceCard({
  run,
  instance,
  onDelete,
}: StrategyInstanceCardProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<string>();
  const isProfit = run.profitLoss >= 0;
  const state = getStrategyRunState(run.mode, run.status);
  const detailUrl = `/strategies/${run.strategy?.id}/runs/${encodeURIComponent(run.id)}`;
  const ModeIcon = {
    BACKTEST: LineChart,
    PAPER: TestTube,
    LIVE: Rocket,
  }[state.mode];
  const toneClasses = {
    slate: {
      bar: 'bg-slate-500',
      icon: 'text-slate-500',
      text: 'text-slate-500',
      tag: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
    },
    blue: {
      bar: 'bg-blue-500',
      icon: 'text-blue-400',
      text: 'text-blue-400',
      tag: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
    },
    emerald: {
      bar: 'bg-emerald-500',
      icon: 'text-emerald-500',
      text: 'text-emerald-500',
      tag: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    },
    amber: {
      bar: 'bg-amber-500',
      icon: 'text-amber-500',
      text: 'text-amber-500',
      tag: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    },
    rose: {
      bar: 'bg-rose-500',
      icon: 'text-rose-500',
      text: 'text-rose-500',
      tag: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
    },
    purple: {
      bar: 'bg-purple-500',
      icon: 'text-purple-400',
      text: 'text-purple-400',
      tag: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
    },
  }[state.color];
  const copyText = (text: string) => {
    if (!navigator.clipboard) return;
    void navigator.clipboard.writeText(text);
  };

  return (
    <>
      <Card
        className="group relative w-full cursor-pointer overflow-hidden rounded-[1.25rem] border border-white/[0.08] bg-[#0B1120]/80 backdrop-blur-2xl transition-all duration-300 hover:border-white/[0.15]"
        onClick={() => setLocation(detailUrl)}
        onContextMenu={event => openAtPointer(event, run.id)}
      >
        {/* Top Status Stripe (Thin) */}
        <div
          className={`absolute top-0 left-0 h-[2px] w-full ${toneClasses.bar}`}
        />

        <div className="flex flex-col gap-4 p-4">
          {/* Header: Identity */}
          <div className="flex items-start justify-between">
            <div className="flex gap-3">
              {/* Status Icon Box */}
              <div
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-all duration-300 ${
                  state.isActive
                    ? 'border-slate-700/50 bg-[#0F1729] text-white'
                    : 'border-white/5 bg-[#0F1729]/50 text-slate-500'
                }`}
              >
                <ModeIcon
                  size={16}
                  strokeWidth={1.5}
                  className={toneClasses.icon}
                />
              </div>

              <div className="flex min-w-0 flex-col justify-between py-0.5">
                <h3 className="truncate pr-2 text-xs font-bold uppercase tracking-wide text-slate-200 transition-colors group-hover:text-blue-400">
                  {instance.displayName}
                </h3>
                <div className="flex items-center gap-2">
                  <span
                    className={cn('text-[10px] font-medium', toneClasses.text)}
                  >
                    {state.statusLabel}
                  </span>
                  <span className="h-0.5 w-0.5 rounded-full bg-slate-600" />
                  <span className="font-mono text-[10px] tracking-wider text-slate-500">
                    {run.id.slice(0, 6)}
                  </span>
                </div>
              </div>
            </div>

            {/* Mode Tag */}
            <div
              className={`rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${toneClasses.tag}`}
            >
              {state.modeLabel}
            </div>
          </div>

          {/* Dashboard: Metrics Grid */}
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-white/[0.05] bg-white/[0.05]">
            {/* Profit */}
            <div className="flex flex-col items-center justify-center gap-0.5 bg-[#0B1120]/50 p-2">
              <span className="text-[9px] font-medium tracking-wider text-slate-500">
                盈亏
              </span>
              <span
                className={`font-mono text-xs font-bold tracking-tight ${
                  isProfit ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {run.profitLoss > 0 ? '+' : ''}
                {run.profitLoss.toFixed(1)}
              </span>
            </div>
            {/* Trades */}
            <div className="flex flex-col items-center justify-center gap-0.5 bg-[#0B1120]/50 p-2">
              <span className="text-[9px] font-medium tracking-wider text-slate-500">
                成交
              </span>
              <span className="font-mono text-xs font-bold tracking-tight text-slate-300">
                {run.totalTrades}
              </span>
            </div>
          </div>

          {/* Footer: Timeline & Actions */}
          <div className="flex items-center justify-between pt-0.5">
            {/* Bound Instrument */}
            <div className="flex -space-x-1.5 overflow-hidden py-1">
              <div className="relative z-10 flex h-5 items-center justify-center rounded-full border border-blue-500/20 bg-blue-500/10 px-2 font-mono text-[9px] font-medium text-blue-300">
                绑定标的 {instance.instrumentCode}
              </div>
            </div>

            {/* Action Buttons */}
            <div
              className="flex items-center gap-1.5"
              onClick={e => e.stopPropagation()}
            >
              {state.canDelete && (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="删除策略实例"
                  title="删除策略实例"
                  className="h-6 w-6 rounded p-0 text-slate-500 transition-colors hover:bg-rose-500/10 hover:text-rose-500"
                  onClick={() => onDelete(run.id)}
                >
                  <Trash2 size={12} />
                </Button>
              )}
              <Button
                size="sm"
                className="h-6 rounded bg-blue-600 px-2 text-[9px] font-bold uppercase tracking-wider text-white shadow-lg shadow-blue-500/20 hover:bg-blue-500"
                onClick={() => setLocation(detailUrl)}
              >
                <Eye className="mr-1 h-3 w-3" />
                {state.listPrimaryAction.label}
              </Button>
            </div>
          </div>
        </div>
      </Card>

      <StudioMenu
        ariaLabel="策略实例菜单"
        items={[
          {
            icon: <Eye className="h-3.5 w-3.5" />,
            id: 'open',
            label: '打开详情',
            onSelect: () => setLocation(detailUrl),
          },
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-run-id',
            label: '复制实例 ID',
            onSelect: () => copyText(run.id),
          },
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-instrument',
            label: '复制标的代码',
            onSelect: () => copyText(instance.instrumentCode),
          },
          { id: 'separator-delete', type: 'separator' },
          {
            danger: true,
            disabled: !state.canDelete,
            icon: <Trash2 className="h-3.5 w-3.5" />,
            id: 'delete',
            label: '删除实例记录',
            onSelect: () => onDelete(run.id),
          },
        ]}
        menu={menu}
        onClose={closeMenu}
        width={192}
      />
    </>
  );
}

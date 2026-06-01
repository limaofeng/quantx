import { Trash2, Eye, LineChart, TestTube, Rocket } from 'lucide-react';
import { useLocation } from 'wouter';

// Badge removed

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

  return (
    <Card
      className="group relative overflow-hidden bg-[#0B1120]/80 backdrop-blur-2xl border border-white/[0.08] hover:border-white/[0.15] rounded-[1.25rem] transition-all duration-300 cursor-pointer w-full"
      onClick={() => setLocation(detailUrl)}
    >
      {/* Top Status Stripe (Thin) */}
      <div
        className={`absolute top-0 left-0 w-full h-[2px] ${toneClasses.bar}`}
      />

      <div className="p-4 flex flex-col gap-4">
        {/* Header: Identity */}
        <div className="flex items-start justify-between">
          <div className="flex gap-3">
            {/* Status Icon Box */}
            <div
              className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 border transition-all duration-300 ${
                state.isActive
                  ? 'bg-[#0F1729] border-slate-700/50 text-white'
                  : 'bg-[#0F1729]/50 border-white/5 text-slate-500'
              }`}
            >
              <ModeIcon
                size={16}
                strokeWidth={1.5}
                className={toneClasses.icon}
              />
            </div>

            <div className="min-w-0 flex flex-col justify-between py-0.5">
              <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wide truncate pr-2 group-hover:text-blue-400 transition-colors">
                {instance.displayName}
              </h3>
              <div className="flex items-center gap-2">
                <span
                  className={cn('text-[10px] font-medium', toneClasses.text)}
                >
                  {state.statusLabel}
                </span>
                <span className="w-0.5 h-0.5 rounded-full bg-slate-600" />
                <span className="text-[10px] text-slate-500 font-mono tracking-wider">
                  {run.id.slice(0, 6)}
                </span>
              </div>
            </div>
          </div>

          {/* Mode Tag */}
          <div
            className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border ${toneClasses.tag}`}
          >
            {state.modeLabel}
          </div>
        </div>

        {/* Dashboard: Metrics Grid */}
        <div className="grid grid-cols-2 gap-px bg-white/[0.05] rounded-lg overflow-hidden border border-white/[0.05]">
          {/* Profit */}
          <div className="bg-[#0B1120]/50 p-2 flex flex-col gap-0.5 items-center justify-center">
            <span className="text-[9px] font-medium text-slate-500 tracking-wider">
              盈亏
            </span>
            <span
              className={`text-xs font-mono font-bold tracking-tight ${
                isProfit ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {run.profitLoss > 0 ? '+' : ''}
              {run.profitLoss.toFixed(1)}
            </span>
          </div>
          {/* Trades */}
          <div className="bg-[#0B1120]/50 p-2 flex flex-col gap-0.5 items-center justify-center">
            <span className="text-[9px] font-medium text-slate-500 tracking-wider">
              成交
            </span>
            <span className="text-xs font-mono font-bold text-slate-300 tracking-tight">
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
                className="h-6 w-6 p-0 rounded text-slate-500 hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                onClick={() => onDelete(run.id)}
              >
                <Trash2 size={12} />
              </Button>
            )}
            <Button
              size="sm"
              className="h-6 px-2 rounded bg-blue-600 hover:bg-blue-500 text-white text-[9px] font-bold uppercase tracking-wider shadow-lg shadow-blue-500/20"
              onClick={() => setLocation(detailUrl)}
            >
              <Eye className="mr-1 h-3 w-3" />
              {state.listPrimaryAction.label}
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

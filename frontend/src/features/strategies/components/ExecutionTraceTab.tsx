import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Copy,
  FileText,
  GitCommitHorizontal,
  LayoutGrid,
} from 'lucide-react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Card } from '@/components/ui/card';

import type { ExecutionTraceView, StrategyInstance } from '../domain';

interface ExecutionTraceTabProps {
  instance?: StrategyInstance | null;
  traces: ExecutionTraceView[];
}

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

function Stage({
  label,
  value,
  tone,
}: {
  label: string;
  value?: string | null;
  tone: 'red' | 'amber' | 'emerald' | 'slate';
}) {
  const Icon = value ? CheckCircle2 : Circle;
  const color = {
    red: 'text-red-500 bg-red-500/10 border-red-500/20',
    amber: 'text-amber-500 bg-amber-500/10 border-amber-500/20',
    emerald: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20',
    slate: 'text-slate-500 bg-slate-500/10 border-slate-500/20',
  }[tone];

  return (
    <div className={`rounded-xl border px-4 py-3 ${color}`}>
      <div className="mb-2 flex items-center gap-2">
        <Icon className="h-3.5 w-3.5" />
        <span className="text-[8px] font-black uppercase tracking-[0.2em]">
          {label}
        </span>
      </div>
      <div className="min-h-[18px] truncate text-[11px] font-black">
        {value || '未返回'}
      </div>
    </div>
  );
}

export default function ExecutionTraceTab({
  instance,
  traces,
}: ExecutionTraceTabProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<ExecutionTraceView>();

  if (!instance) {
    return (
      <Card className="p-10 text-center">
        <p className="text-sm font-bold text-slate-500">请先选择策略实例。</p>
      </Card>
    );
  }

  if (traces.length === 0) {
    return (
      <Card className="rounded-[2rem] border border-dashed border-slate-200 bg-white p-12 text-center shadow-xl dark:border-white/10 dark:bg-slate-900/60">
        <GitCommitHorizontal className="mx-auto mb-5 h-10 w-10 text-slate-300" />
        <h3 className="mb-2 text-sm font-black uppercase tracking-[0.2em] text-slate-700 dark:text-slate-200">
          暂无执行跟踪
        </h3>
        <p className="mx-auto max-w-lg text-xs font-medium leading-relaxed text-slate-500">
          执行跟踪只展示风控、OrderSizer、委托和成交状态流；策略意图不会在这里被当作成交。
        </p>
      </Card>
    );
  }

  return (
    <>
      <div className="space-y-3">
        <Card className="rounded-lg border border-white/10 bg-[#0b1120]/70 p-4 shadow-none">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-[9px] font-black uppercase tracking-[0.3em] text-red-500">
                执行跟踪
              </div>
              <h3 className="mt-1 text-lg font-black text-slate-900 dark:text-white">
                {instance.instrumentCode}
              </h3>
            </div>
            <div className="flex items-center gap-2 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[10px] font-bold text-amber-500">
              <AlertTriangle className="h-3.5 w-3.5" />
              策略意图、风控、委托、成交分层展示
            </div>
          </div>
        </Card>

        {traces.map(trace => (
          <Card
            key={trace.id}
            className="rounded-lg border border-white/10 bg-[#0b1120]/70 p-4 shadow-none transition-colors hover:border-red-500/25 hover:bg-white/[0.04]"
            onContextMenu={event => openAtPointer(event, trace)}
          >
            <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-3">
                <div className="rounded-lg bg-red-500/10 px-2.5 py-1 text-[9px] font-black uppercase tracking-widest text-red-500">
                  策略意图
                </div>
                <div className="font-mono text-xs font-black text-slate-700 dark:text-slate-200">
                  {trace.intentId}
                </div>
              </div>
              <div className="text-[10px] font-bold text-slate-500">
                {trace.instrumentCode} · {trace.side}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
              <Stage label="风控" value={trace.riskDecision} tone="amber" />
              <Stage
                label="OrderSizer"
                value={trace.sizingResult}
                tone="red"
              />
              <Stage label="委托" value={trace.orderStatus} tone="slate" />
              <Stage label="成交" value={trace.fillStatus} tone="emerald" />
            </div>

            {trace.reason && (
              <div className="mt-4 rounded-xl bg-slate-50 px-4 py-3 text-xs font-medium text-slate-500 dark:bg-white/[0.03]">
                {trace.reason}
              </div>
            )}
          </Card>
        ))}
      </div>

      <StudioMenu
        ariaLabel="执行跟踪菜单"
        menu={menu}
        onClose={closeMenu}
        width={216}
        items={[
          {
            id: 'open-stock',
            label: '查看标的详情',
            icon: <LayoutGrid size={14} />,
            onSelect: () =>
              menu?.payload?.instrumentCode &&
              setLocation(`/stock/${menu.payload.instrumentCode}`),
          },
          {
            id: 'copy-intent',
            label: '复制意图 ID',
            icon: <Copy size={14} />,
            onSelect: () => copyText(menu?.payload?.intentId),
          },
          {
            id: 'copy-order',
            label: '复制委托 ID',
            icon: <Copy size={14} />,
            disabled: !menu?.payload?.orderId,
            onSelect: () => copyText(menu?.payload?.orderId),
          },
          {
            id: 'copy-trace',
            label: '复制 Trace ID',
            icon: <Copy size={14} />,
            disabled: !menu?.payload?.traceId,
            onSelect: () => copyText(menu?.payload?.traceId),
          },
          { id: 'sep', type: 'separator' },
          {
            id: 'copy-reason',
            label: '复制原因',
            icon: <FileText size={14} />,
            disabled: !menu?.payload?.reason,
            onSelect: () => copyText(menu?.payload?.reason),
          },
        ]}
      />
    </>
  );
}

import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  LoaderCircle,
} from 'lucide-react';

import { cn } from '@/utils/cn';

const STATUS_META: Record<
  string,
  {
    className: string;
    icon: typeof CircleCheck;
    label: string;
  }
> = {
  failed: {
    className: 'border-rose-500/25 bg-rose-500/10 text-rose-300',
    icon: CircleAlert,
    label: '失败',
  },
  running: {
    className: 'border-sky-500/25 bg-sky-500/10 text-sky-300',
    icon: LoaderCircle,
    label: '运行中',
  },
  success: {
    className: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300',
    icon: CircleCheck,
    label: '成功',
  },
};

export function ResearchStatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const statusFamily = normalized.startsWith('failed')
    ? 'failed'
    : normalized.startsWith('running')
      ? 'running'
      : normalized;
  const meta = STATUS_META[statusFamily] || {
    className: 'border-slate-500/25 bg-slate-500/10 text-slate-300',
    icon: CircleDashed,
    label: status || '未知',
  };
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded border px-1.5 text-[10px] font-bold',
        meta.className
      )}
    >
      <Icon
        className={cn(
          'h-3 w-3',
          statusFamily === 'running' &&
            'animate-spin motion-reduce:animate-none'
        )}
      />
      {meta.label}
    </span>
  );
}

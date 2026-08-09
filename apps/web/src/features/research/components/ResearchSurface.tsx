import {
  AlertTriangle,
  DatabaseZap,
  FileWarning,
  LoaderCircle,
  RefreshCw,
} from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/utils/cn';

export function ResearchPanel({
  children,
  className,
  description,
  title,
}: {
  children: ReactNode;
  className?: string;
  description?: string;
  title: string;
}) {
  return (
    <section
      className={cn(
        'overflow-hidden rounded-lg border border-white/[0.07] bg-[#0d1728]/80',
        className
      )}
    >
      <header className="border-b border-white/[0.06] px-4 py-3">
        <h2 className="text-xs font-black tracking-wide text-slate-100">
          {title}
        </h2>
        {description && (
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            {description}
          </p>
        )}
      </header>
      {children}
    </section>
  );
}

export function ResearchLoadingState({ label }: { label: string }) {
  return (
    <div
      className="flex min-h-[20rem] flex-col items-center justify-center gap-3 text-slate-500"
      role="status"
    >
      <LoaderCircle className="h-5 w-5 animate-spin text-red-400 motion-reduce:animate-none" />
      <span className="text-xs font-semibold">{label}</span>
    </div>
  );
}

export function ResearchErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="mx-auto flex min-h-[20rem] max-w-xl flex-col items-center justify-center px-6 text-center"
      role="alert"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-rose-500/25 bg-rose-500/10 text-rose-300">
        <FileWarning className="h-4 w-4" />
      </div>
      <h2 className="mt-4 text-sm font-bold text-slate-100">
        研究结果加载失败
      </h2>
      <p className="mt-2 max-w-md text-xs leading-5 text-slate-500">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 text-xs font-bold text-slate-300 transition-colors hover:border-red-500/40 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        重试
      </button>
    </div>
  );
}

export function ResearchEmptyState({
  description,
  title,
}: {
  description: string;
  title: string;
}) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-6 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-slate-500">
        <DatabaseZap className="h-4 w-4" />
      </div>
      <h3 className="mt-3 text-xs font-bold text-slate-300">{title}</h3>
      <p className="mt-1 max-w-md text-[11px] leading-5 text-slate-600">
        {description}
      </p>
    </div>
  );
}

export function WarningStrip({
  children,
  tone = 'amber',
}: {
  children: ReactNode;
  tone?: 'amber' | 'rose';
}) {
  return (
    <div
      className={cn(
        'flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-xs leading-5',
        tone === 'rose'
          ? 'border-rose-500/25 bg-rose-500/[0.08] text-rose-200'
          : 'border-amber-500/25 bg-amber-500/[0.08] text-amber-100'
      )}
      role="alert"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

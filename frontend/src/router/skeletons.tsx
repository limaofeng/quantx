import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/utils/cn';

export type RouteSkeletonVariant =
  | 'default'
  | 'dashboard'
  | 'studio'
  | 'table'
  | 'detail'
  | 'form';

interface RouteSkeletonProps {
  variant?: RouteSkeletonVariant;
}

function MetricSkeletonGrid() {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <div
          key={index}
          className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-white/5 dark:bg-white/[0.03]"
        >
          <div className="flex items-center justify-between">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-8 w-8 rounded-lg" />
          </div>
          <Skeleton className="mt-5 h-7 w-32" />
          <Skeleton className="mt-3 h-2.5 w-20" />
        </div>
      ))}
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm dark:border-white/5 dark:bg-white/[0.03]">
      <div className="flex flex-col gap-4 border-b border-slate-200 p-5 dark:border-white/5 md:flex-row md:items-center md:justify-between">
        <div className="space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-64 max-w-full" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-9 w-28" />
          <Skeleton className="h-9 w-20" />
        </div>
      </div>
      <div className="divide-y divide-slate-100 dark:divide-white/5">
        {Array.from({ length: 8 }).map((_, index) => (
          <div
            key={index}
            className="grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-4 px-5 py-4"
          >
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-3/5" />
            <Skeleton className="h-4 w-16 justify-self-end" />
          </div>
        ))}
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-white/5 dark:bg-white/[0.03]">
        <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <Skeleton className="h-14 w-14 rounded-xl" />
            <div className="min-w-0 space-y-3">
              <Skeleton className="h-7 w-64 max-w-full" />
              <Skeleton className="h-3 w-96 max-w-full" />
            </div>
          </div>
          <Skeleton className="h-10 w-32" />
        </div>
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr]">
        <Skeleton className="h-72 rounded-lg" />
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-white/5 dark:bg-white/[0.03]">
          {Array.from({ length: 5 }).map((_, index) => (
            <div
              key={index}
              className="flex items-center justify-between gap-4"
            >
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-4 w-28" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FormSkeleton() {
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-white/5 dark:bg-white/[0.03]">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="mt-4 h-9 w-80 max-w-full" />
        <Skeleton className="mt-3 h-3 w-[30rem] max-w-full" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-white/5 dark:bg-white/[0.03]">
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="space-y-2">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-11 w-full" />
            </div>
          ))}
        </div>
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-white/5 dark:bg-white/[0.03]">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <MetricSkeletonGrid />
      <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
        <Skeleton className="h-80 rounded-lg" />
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-white/5 dark:bg-white/[0.03]">
          <Skeleton className="h-5 w-36" />
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="flex items-center gap-3">
              <Skeleton className="h-9 w-9 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3 w-3/5" />
                <Skeleton className="h-2.5 w-2/5" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StudioSkeleton() {
  return (
    <div
      data-studio-workbench
      className="flex h-full min-h-[30rem] overflow-hidden bg-[var(--studio-bg)] text-slate-200"
    >
      <div className="flex w-12 shrink-0 flex-col items-center gap-2.5 border-r border-white/5 bg-[#0b1120] py-4">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton
            key={index}
            className="h-8 w-8 rounded-lg bg-white/[0.04]"
          />
        ))}
        <div className="mt-auto flex w-full flex-col items-center gap-2.5 border-t border-white/5 pt-2.5">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton
              key={index}
              className="h-8 w-8 rounded-lg bg-white/[0.04]"
            />
          ))}
          <Skeleton className="h-8 w-8 rounded-lg border border-red-500/20 bg-red-500/10" />
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col bg-[#0b1120]/20">
        <div className="flex h-10 shrink-0 items-center border-b border-white/5 bg-[#0f172a] px-3">
          <Skeleton className="h-7 w-28 rounded-md bg-red-500/10" />
        </div>
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-4">
          <div className="min-w-0 space-y-2">
            <Skeleton className="h-3 w-32 bg-white/[0.06]" />
            <Skeleton className="h-2.5 w-64 max-w-full bg-white/[0.04]" />
          </div>
          <Skeleton className="hidden h-3 w-36 bg-white/[0.04] md:block" />
        </div>
        <div className="min-h-0 flex-1 overflow-hidden p-3">
          <div className="grid h-full grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0 space-y-3">
              <Skeleton className="h-36 rounded-lg border border-white/10 bg-white/[0.03]" />
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {Array.from({ length: 7 }).map((_, index) => (
                  <Skeleton
                    key={index}
                    className="h-[104px] rounded-lg border border-white/5 bg-white/[0.025]"
                  />
                ))}
              </div>
            </div>
            <Skeleton className="hidden rounded-lg border border-white/10 bg-white/[0.03] lg:block" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function RouteSkeleton({ variant = 'default' }: RouteSkeletonProps) {
  return (
    <div
      className={cn(
        'w-full animate-in fade-in duration-200',
        variant === 'studio'
          ? 'h-full space-y-0'
          : variant === 'form'
            ? 'pb-16'
            : 'space-y-5'
      )}
      aria-label="页面加载中"
      role="status"
    >
      {variant === 'dashboard' && <DashboardSkeleton />}
      {variant === 'studio' && <StudioSkeleton />}
      {variant === 'table' && <TableSkeleton />}
      {variant === 'detail' && <DetailSkeleton />}
      {variant === 'form' && <FormSkeleton />}
      {variant === 'default' && (
        <>
          <MetricSkeletonGrid />
          <TableSkeleton />
        </>
      )}
    </div>
  );
}

import type { ReactNode } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/utils/cn';

export type RouteSkeletonVariant =
  'default' | 'dashboard' | 'studio' | 'table' | 'detail' | 'form';

interface RouteSkeletonProps {
  variant?: RouteSkeletonVariant;
}

const panelClass =
  'rounded-lg border border-white/[0.07] bg-[#0d1727]/75 shadow-[inset_0_1px_0_rgba(255,255,255,0.015)]';

function SkeletonPanel({
  children,
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return <div className={cn(panelClass, className)}>{children}</div>;
}

function ContentHeaderSkeleton() {
  return (
    <div className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-white/[0.06] bg-[#0b1120]/90 px-4">
      <div className="min-w-0 space-y-1.5">
        <Skeleton className="h-3 w-32 max-w-[42vw]" />
        <Skeleton className="h-2 w-56 max-w-[58vw]" />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Skeleton className="hidden h-2.5 w-24 sm:block" />
        <Skeleton className="h-7 w-16 rounded-md" />
      </div>
    </div>
  );
}

function MetricSkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <SkeletonPanel
          key={index}
          className="flex min-h-[90px] flex-col justify-between p-4"
        >
          <div className="flex items-center justify-between gap-4">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="h-8 w-8 rounded-lg" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-6 w-28 max-w-[65%]" />
            <Skeleton className="h-2 w-16" />
          </div>
        </SkeletonPanel>
      ))}
    </div>
  );
}

function CompactListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="divide-y divide-white/[0.045]">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-3 px-3 py-2.5">
          <Skeleton className="h-7 w-7 shrink-0 rounded-md" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-2.5 w-3/5" />
            <Skeleton className="h-2 w-2/5" />
          </div>
          <Skeleton className="h-2.5 w-12 shrink-0" />
        </div>
      ))}
    </div>
  );
}

function TablePanelSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <SkeletonPanel className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex h-10 shrink-0 items-center justify-between gap-4 border-b border-white/[0.06] px-3">
        <Skeleton className="h-2.5 w-28" />
        <div className="flex items-center gap-2">
          <Skeleton className="hidden h-7 w-36 rounded-md sm:block" />
          <Skeleton className="h-7 w-16 rounded-md" />
        </div>
      </div>
      <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_4.5rem] gap-3 border-b border-white/[0.05] bg-black/10 px-3 py-2 sm:grid-cols-[1.4fr_1fr_1fr_5rem]">
        <Skeleton className="h-2 w-20" />
        <Skeleton className="hidden h-2 w-16 sm:block" />
        <Skeleton className="hidden h-2 w-16 sm:block" />
        <Skeleton className="h-2 w-10 justify-self-end" />
      </div>
      <div className="min-h-0 flex-1 divide-y divide-white/[0.045] overflow-hidden">
        {Array.from({ length: rows }).map((_, index) => (
          <div
            key={index}
            className="grid grid-cols-[minmax(0,1fr)_4.5rem] items-center gap-3 px-3 py-3 sm:grid-cols-[1.4fr_1fr_1fr_5rem]"
          >
            <Skeleton className="h-2.5 w-4/5" />
            <Skeleton className="hidden h-2.5 w-3/5 sm:block" />
            <Skeleton className="hidden h-2.5 w-2/3 sm:block" />
            <Skeleton className="h-2.5 w-12 justify-self-end" />
          </div>
        ))}
      </div>
    </SkeletonPanel>
  );
}

function DashboardSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <div className="space-y-3">
          <MetricSkeletonGrid />
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(18rem,0.8fr)_minmax(28rem,1.2fr)]">
            <SkeletonPanel className="h-36 p-4">
              <Skeleton className="h-3 w-24" />
              <div className="mt-4 grid grid-cols-2 gap-2">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={index} className="h-9 rounded-md" />
                ))}
              </div>
            </SkeletonPanel>
            <SkeletonPanel className="h-36 overflow-hidden">
              <div className="flex h-10 items-center justify-between border-b border-white/[0.05] px-3">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-2.5 w-16" />
              </div>
              <div className="grid grid-cols-1 gap-2 p-3 sm:grid-cols-3">
                {Array.from({ length: 3 }).map((_, index) => (
                  <Skeleton key={index} className="h-16 rounded-md" />
                ))}
              </div>
            </SkeletonPanel>
          </div>
          <SkeletonPanel className="min-h-[172px] overflow-hidden">
            <div className="flex h-10 items-center border-b border-white/[0.05] px-3">
              <Skeleton className="h-3 w-24" />
            </div>
            <CompactListSkeleton rows={4} />
          </SkeletonPanel>
        </div>
      </div>
    </div>
  );
}

function StudioSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="flex min-h-0 min-w-0 flex-col gap-3">
            <SkeletonPanel className="h-36 shrink-0 p-3">
              <div className="flex items-center justify-between">
                <Skeleton className="h-3 w-28" />
                <Skeleton className="h-7 w-20 rounded-md" />
              </div>
              <Skeleton className="mt-5 h-16 w-full rounded-md" />
            </SkeletonPanel>
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 8 }).map((_, index) => (
                <SkeletonPanel key={index} className="min-h-[96px] p-3">
                  <Skeleton className="h-2.5 w-2/5" />
                  <Skeleton className="mt-4 h-5 w-3/5" />
                  <Skeleton className="mt-3 h-2 w-4/5" />
                </SkeletonPanel>
              ))}
            </div>
          </div>
          <SkeletonPanel className="hidden min-h-0 overflow-hidden lg:block">
            <div className="flex h-10 items-center border-b border-white/[0.05] px-3">
              <Skeleton className="h-3 w-24" />
            </div>
            <CompactListSkeleton rows={7} />
          </SkeletonPanel>
        </div>
      </div>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="flex min-h-0 flex-1 p-3">
        <TablePanelSkeleton />
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.55fr)_minmax(18rem,0.75fr)]">
          <div className="flex min-h-0 flex-col gap-3">
            <SkeletonPanel className="flex h-20 shrink-0 items-center gap-3 p-3">
              <Skeleton className="h-12 w-12 shrink-0 rounded-lg" />
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-4 w-48 max-w-[70%]" />
                <Skeleton className="h-2.5 w-72 max-w-[90%]" />
              </div>
              <Skeleton className="hidden h-8 w-24 rounded-md sm:block" />
            </SkeletonPanel>
            <SkeletonPanel className="min-h-[180px] flex-1 p-3">
              <div className="flex items-center justify-between">
                <Skeleton className="h-3 w-28" />
                <Skeleton className="h-7 w-24 rounded-md" />
              </div>
              <Skeleton className="mt-4 h-[calc(100%_-_2.75rem)] min-h-28 w-full rounded-md" />
            </SkeletonPanel>
            <div className="grid shrink-0 grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <SkeletonPanel key={index} className="h-16 p-3">
                  <Skeleton className="h-2 w-3/5" />
                  <Skeleton className="mt-3 h-3.5 w-4/5" />
                </SkeletonPanel>
              ))}
            </div>
          </div>
          <SkeletonPanel className="hidden min-h-0 overflow-hidden lg:block">
            <div className="flex h-10 items-center border-b border-white/[0.05] px-3">
              <Skeleton className="h-3 w-24" />
            </div>
            <CompactListSkeleton rows={8} />
          </SkeletonPanel>
        </div>
      </div>
    </div>
  );
}

function FormFieldsSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="space-y-2">
          <Skeleton className="h-2.5 w-24" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
      ))}
    </div>
  );
}

function FormSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <div className="mx-auto grid h-full min-h-0 max-w-6xl grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <SkeletonPanel className="min-h-0 p-4">
            <div className="mb-5 space-y-2 border-b border-white/[0.05] pb-4">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-2.5 w-72 max-w-full" />
            </div>
            <FormFieldsSkeleton />
          </SkeletonPanel>
          <SkeletonPanel className="hidden min-h-0 p-4 lg:block">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="mt-4 h-24 w-full rounded-md" />
            <div className="mt-4 space-y-3">
              <Skeleton className="h-2.5 w-4/5" />
              <Skeleton className="h-2.5 w-3/5" />
              <Skeleton className="h-2.5 w-2/3" />
            </div>
            <Skeleton className="mt-6 h-9 w-full rounded-md" />
          </SkeletonPanel>
        </div>
      </div>
    </div>
  );
}

function DefaultSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <ContentHeaderSkeleton />
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
        <MetricSkeletonGrid />
        <TablePanelSkeleton rows={5} />
      </div>
    </div>
  );
}

export function RouteSkeleton({ variant = 'default' }: RouteSkeletonProps) {
  return (
    <div
      aria-busy="true"
      aria-live="polite"
      className="h-full min-h-0 w-full overflow-hidden"
      data-testid={`route-skeleton-${variant}`}
      role="status"
    >
      <span className="sr-only">页面加载中</span>
      <div aria-hidden="true" className="h-full min-h-0">
        {variant === 'dashboard' && <DashboardSkeleton />}
        {variant === 'studio' && <StudioSkeleton />}
        {variant === 'table' && <TableSkeleton />}
        {variant === 'detail' && <DetailSkeleton />}
        {variant === 'form' && <FormSkeleton />}
        {variant === 'default' && <DefaultSkeleton />}
      </div>
    </div>
  );
}

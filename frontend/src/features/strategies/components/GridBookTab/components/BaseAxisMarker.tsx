import { formatNumber } from '../formatters';

interface BaseAxisMarkerProps {
  basePrice?: number | null;
}

export function BaseAxisMarker({ basePrice }: BaseAxisMarkerProps) {
  return (
    <div className="relative z-20 grid grid-cols-[68px_minmax(0,1fr)] items-center gap-3 py-2">
      <div className="relative flex min-h-12 items-center justify-center">
        <div className="absolute left-1/2 h-full w-px -translate-x-1/2 bg-blue-500/60" />
        <div className="relative rounded-lg border border-blue-400/40 bg-white px-2.5 py-1.5 text-center shadow-[0_0_18px_rgba(59,130,246,0.22)] dark:bg-slate-950">
          <div className="text-[7px] font-black uppercase text-blue-500 dark:text-blue-300">
            基准价
          </div>
          <div className="mt-0.5 font-mono text-[11px] font-black text-slate-950 dark:text-white">
            {formatNumber(basePrice || 0)}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-gradient-to-r from-transparent via-blue-500/45 to-blue-500/20" />
        <div className="h-px flex-1 bg-gradient-to-l from-transparent via-blue-500/45 to-blue-500/20" />
      </div>
    </div>
  );
}

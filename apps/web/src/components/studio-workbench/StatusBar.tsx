import type { StatusBarProps } from './types';

export function StatusBar({ left, right }: StatusBarProps) {
  return (
    <div
      className="flex h-[26px] shrink-0 items-center justify-between overflow-hidden border-t border-white/10 bg-slate-950/90 px-3 text-[11px] font-bold uppercase leading-none tracking-wider text-slate-400"
      data-testid="studio-status-bar"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden whitespace-nowrap">
        {left}
      </div>
      <div className="ml-3 flex min-w-0 flex-1 items-center justify-end gap-3 overflow-hidden whitespace-nowrap">
        {right}
      </div>
    </div>
  );
}

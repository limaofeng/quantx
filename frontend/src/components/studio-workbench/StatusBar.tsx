import type { StatusBarProps } from './types';

export function StatusBar({ left, right }: StatusBarProps) {
  return (
    <div
      className="flex h-[22px] shrink-0 items-center justify-between overflow-hidden border-t border-white/5 bg-slate-950/85 px-3 text-[10px] font-bold uppercase leading-none tracking-wider text-slate-500"
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

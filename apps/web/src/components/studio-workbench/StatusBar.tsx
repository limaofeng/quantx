import type { StatusBarProps } from './types';

export function StatusBar({ left, right }: StatusBarProps) {
  return (
    <div
      className="relative flex h-10 shrink-0 items-center justify-between overflow-hidden border-t border-white/10 bg-[#0b1120] px-3 text-[10px] font-medium leading-none text-slate-400"
      data-testid="studio-status-bar"
    >
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-slate-500/20 to-transparent"
      />
      <div
        className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-left"
      >
        {left}
      </div>
      <div
        className="ml-4 flex min-w-0 flex-1 items-center justify-end gap-3 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-right"
      >
        {right}
      </div>
    </div>
  );
}

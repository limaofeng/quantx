import type { StatusBarProps } from './types';

export function StatusBar({ left, right }: StatusBarProps) {
  return (
    <div
      className="relative flex h-[38px] shrink-0 items-center justify-between overflow-hidden border-t border-white/10 bg-[#07111f] px-4 text-[11px] font-medium leading-none text-slate-400 [&_.text-slate-700]:hidden"
      data-testid="studio-status-bar"
    >
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-slate-500/20 to-transparent"
      />
      <div
        className="flex min-w-0 flex-1 items-center gap-4 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-left"
      >
        {left}
      </div>
      <div
        className="ml-5 flex min-w-0 flex-1 items-center justify-end gap-4 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-right"
      >
        {right}
      </div>
    </div>
  );
}

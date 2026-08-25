import { cn } from '@/utils/cn';

import type { StatusBarProps } from './types';

export function StatusBar({
  left,
  right,
  variant = 'default',
}: StatusBarProps) {
  const isWorkspaceVariant = variant === 'workspace';

  return (
    <div
      className={cn(
        'relative flex h-studio-status shrink-0 items-center justify-between overflow-hidden border-t border-white/10 bg-[#07111f] px-3 text-ui-micro font-medium text-slate-400',
        isWorkspaceVariant
          ? 'studio-shell-status-bar px-3 text-slate-400'
          : '[&_.text-slate-700]:hidden'
      )}
      data-variant={variant}
      data-testid="studio-status-bar"
      style={
        isWorkspaceVariant
          ? {
              background: 'linear-gradient(180deg, #071321 0%, #040b15 100%)',
              borderColor: 'rgba(111, 151, 194, 0.2)',
              boxShadow:
                'inset 0 1px 0 rgba(126, 169, 212, 0.07), 0 -8px 24px rgba(0, 0, 0, 0.12)',
            }
          : undefined
      }
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-slate-500/20 to-transparent',
          isWorkspaceVariant && 'via-slate-400/20'
        )}
      />
      <div
        className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-left"
      >
        {left}
      </div>
      <div
        className="ml-3 flex min-w-0 flex-1 items-center justify-end gap-3 overflow-hidden whitespace-nowrap"
        data-testid="studio-status-right"
      >
        {right}
      </div>
    </div>
  );
}

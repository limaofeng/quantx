import { ChevronLeft } from 'lucide-react';

import { cn } from '@/utils/cn';

import { getStudioThemeStyles } from './themeStyles';
import type { ActivityBarProps, StudioAction, StudioMode } from './types';

export function ActivityBar({
  activeMode,
  globalActions = [],
  modes,
  onExit,
  onModeChange,
  theme,
  utilityActions = [],
}: ActivityBarProps) {
  const themeStyles = getStudioThemeStyles(theme.name);
  const ServiceIcon = theme.icon;
  const renderActionButton = (
    action: StudioAction,
    section: 'global' | 'utility'
  ) => {
    const Icon = action.icon;
    const isActive = Boolean(action.active);

    return (
      <button
        key={action.id}
        type="button"
        onClick={action.onSelect}
        onFocus={action.onHover}
        onMouseEnter={action.onHover}
        className={cn(
          'group relative flex h-8 w-8 items-center justify-center rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2',
          themeStyles.focusRing,
          isActive
            ? themeStyles.activeButton
            : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
        )}
        title={action.label}
        aria-label={action.label}
        aria-pressed={isActive || undefined}
        data-studio-action-id={action.id}
        data-studio-action-section={section}
        data-testid="studio-action-button"
      >
        <Icon size={18} />
        {action.badge && (
          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-rose-400 ring-2 ring-[#0b1120]" />
        )}
        {isActive && (
          <span
            className={cn(
              'absolute -left-2 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-full',
              themeStyles.activeIndicator
            )}
          />
        )}
        <span className="pointer-events-none absolute left-full z-50 ml-2 whitespace-nowrap rounded-lg border border-white/10 bg-slate-800 px-2.5 py-1.5 text-xs font-bold text-white opacity-0 shadow-xl transition-opacity group-hover:opacity-100">
          {action.label}
        </span>
      </button>
    );
  };

  const renderModeButton = (mode: StudioMode) => {
    const Icon = mode.icon;
    const isActive = activeMode === mode.id;
    const isDisabled = Boolean(mode.disabled);

    return (
      <button
        key={mode.id}
        type="button"
        aria-disabled={isDisabled}
        onClick={() => {
          if (!isDisabled) onModeChange(mode.id);
        }}
        className={cn(
          'group relative flex h-8 w-8 items-center justify-center rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2',
          themeStyles.focusRing,
          isActive
            ? themeStyles.activeButton
            : isDisabled
              ? 'cursor-not-allowed text-slate-700'
              : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
        )}
        title={mode.label}
        aria-label={mode.label}
        aria-pressed={isActive}
        data-studio-mode-id={mode.id}
        data-testid="studio-mode-button"
      >
        <Icon size={18} />
        {isActive && (
          <span
            className={cn(
              'absolute -left-2 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-full',
              themeStyles.activeIndicator
            )}
          />
        )}
        <span className="pointer-events-none absolute left-full z-50 ml-2 whitespace-nowrap rounded-lg border border-white/10 bg-slate-800 px-2.5 py-1.5 text-xs font-bold text-white opacity-0 shadow-xl transition-opacity group-hover:opacity-100">
          {mode.disabledReason || mode.label}
        </span>
      </button>
    );
  };

  return (
    <div
      className="flex w-12 shrink-0 flex-col items-center gap-2.5 border-r border-white/5 bg-[#0b1120] py-4"
      data-testid="studio-activity-bar"
    >
      {onExit && (
        <button
          type="button"
          onClick={onExit}
          className={cn(
            'group flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-all hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2',
            'mb-1',
            themeStyles.focusRing
          )}
          title="返回"
        >
          <ChevronLeft
            size={18}
            className="transition-transform group-hover:-translate-x-0.5"
          />
        </button>
      )}

      <div className="flex min-h-0 flex-1 flex-col items-center gap-2.5">
        {globalActions.length > 0 ? (
          <div className="flex flex-col items-center gap-2.5">
            {globalActions.map(action => renderActionButton(action, 'global'))}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2.5">
            {modes.map(renderModeButton)}
          </div>
        )}
      </div>

      <div
        className="mt-auto flex w-full flex-col items-center gap-2.5 border-t border-white/5 pt-2.5"
        data-testid="studio-utility-bar"
      >
        {utilityActions.map(action => renderActionButton(action, 'utility'))}
        <div
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-lg border',
            themeStyles.iconBox
          )}
          title={theme.title}
          data-testid="studio-service-logo"
        >
          <ServiceIcon size={18} />
        </div>
      </div>
    </div>
  );
}

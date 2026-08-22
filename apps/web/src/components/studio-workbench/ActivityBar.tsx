import { ChevronLeft } from 'lucide-react';

import { cn } from '@/utils/cn';

import { STUDIO_CHROME_BACKGROUND } from './studioShellStyles';
import { getStudioThemeStyles } from './themeStyles';
import type { ActivityBarProps, StudioAction, StudioMode } from './types';

export function ActivityBar({
  activeMode,
  environmentStatus,
  globalActions = [],
  modes,
  onExit,
  onModeChange,
  theme,
  utilityActions = [],
  variant = 'compact',
}: ActivityBarProps) {
  const themeStyles = getStudioThemeStyles(theme.name);
  const ServiceIcon = theme.icon;
  const isStudioVariant = variant === 'studio';
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
          'group relative flex transition-all focus-visible:outline-none focus-visible:ring-2',
          themeStyles.focusRing,
          isStudioVariant
            ? cn(
                'w-16 flex-col items-center justify-center gap-1 rounded-md border border-transparent px-1',
                isActive
                  ? 'text-slate-100'
                  : 'text-slate-500 hover:border-white/5 hover:bg-white/5 hover:text-slate-200'
              )
            : cn(
                'h-8 w-8 items-center justify-center rounded-lg',
                isActive
                  ? themeStyles.activeButton
                  : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
              )
        )}
        title={action.label}
        aria-label={action.label}
        aria-pressed={isActive || undefined}
        data-studio-action-id={action.id}
        data-studio-action-section={section}
        data-testid="studio-action-button"
        style={
          isStudioVariant
            ? {
                height: 'clamp(3.5rem, 7.6vh, 4.5rem)',
                ...(isActive
                  ? {
                      background: '#0c1a2b',
                      borderColor: '#21364e',
                      boxShadow: 'inset 0 1px 0 rgba(148, 190, 230, 0.06)',
                    }
                  : {}),
              }
            : undefined
        }
      >
        <Icon size={isStudioVariant ? 21 : 18} strokeWidth={2} />
        {action.badge && (
          <span
            className={cn(
              'absolute h-1.5 w-1.5 rounded-full bg-rose-400 ring-2 ring-[#0b1120]',
              isStudioVariant ? 'right-3 top-1.5' : 'right-1.5 top-1.5'
            )}
          />
        )}
        {isActive && (
          <span
            className={cn(
              'absolute top-1/2 -translate-y-1/2 rounded-r-full',
              isStudioVariant ? '-left-2 h-7 w-0.5' : '-left-2 h-4 w-1',
              themeStyles.activeIndicator
            )}
          />
        )}
        {isStudioVariant ? (
          <span className="max-w-full truncate text-[11px] font-medium leading-none tracking-wide">
            {action.shortLabel || action.label}
          </span>
        ) : (
          <span className="pointer-events-none absolute left-full z-50 ml-2 whitespace-nowrap rounded-lg border border-white/10 bg-slate-800 px-2.5 py-1.5 text-xs font-bold text-white opacity-0 shadow-xl transition-opacity group-hover:opacity-100">
            {action.label}
          </span>
        )}
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
          'group relative flex transition-all focus-visible:outline-none focus-visible:ring-2',
          themeStyles.focusRing,
          isStudioVariant
            ? cn(
                'w-16 flex-col items-center justify-center gap-1.5 rounded-md border border-transparent px-1',
                isActive
                  ? 'text-slate-100'
                  : isDisabled
                    ? 'cursor-not-allowed text-slate-700'
                    : 'text-slate-500 hover:border-white/5 hover:bg-white/5 hover:text-slate-200'
              )
            : cn(
                'h-8 w-8 items-center justify-center rounded-lg',
                isActive
                  ? themeStyles.activeButton
                  : isDisabled
                    ? 'cursor-not-allowed text-slate-700'
                    : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
              )
        )}
        title={mode.label}
        aria-label={mode.label}
        aria-pressed={isActive}
        data-studio-mode-id={mode.id}
        data-testid="studio-mode-button"
        style={
          isStudioVariant
            ? {
                height: 'clamp(3.5rem, 7.6vh, 4.5rem)',
                ...(isActive
                  ? {
                      background: '#0c1a2b',
                      borderColor: '#21364e',
                      boxShadow: 'inset 0 1px 0 rgba(148, 190, 230, 0.06)',
                    }
                  : {}),
              }
            : undefined
        }
      >
        <Icon size={isStudioVariant ? 21 : 18} strokeWidth={2} />
        {isActive && (
          <span
            className={cn(
              'absolute top-1/2 -translate-y-1/2 rounded-r-full',
              isStudioVariant ? '-left-2 h-7 w-0.5' : '-left-2 h-4 w-1',
              themeStyles.activeIndicator
            )}
          />
        )}
        {isStudioVariant ? (
          <span className="max-w-full truncate text-[11px] font-medium leading-none tracking-wide">
            {mode.label}
          </span>
        ) : (
          <span className="pointer-events-none absolute left-full z-50 ml-2 whitespace-nowrap rounded-lg border border-white/10 bg-slate-800 px-2.5 py-1.5 text-xs font-bold text-white opacity-0 shadow-xl transition-opacity group-hover:opacity-100">
            {mode.disabledReason || mode.label}
          </span>
        )}
      </button>
    );
  };

  return (
    <aside
      aria-label={isStudioVariant ? 'Studio 主导航' : `${theme.title} 导航`}
      className={cn(
        'flex shrink-0 flex-col items-center',
        isStudioVariant
          ? 'studio-shell-activity-bar gap-1 py-2'
          : 'w-12 gap-2.5 border-r border-white/5 bg-[#0b1120] py-4'
      )}
      data-testid="studio-activity-bar"
      data-variant={variant}
      style={
        isStudioVariant
          ? {
              background: STUDIO_CHROME_BACKGROUND,
              position: 'relative',
              width: 84,
              zIndex: 20,
            }
          : undefined
      }
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

      {!isStudioVariant && (
        <>
          <div
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
              themeStyles.iconBox
            )}
            title={theme.title}
            data-testid="studio-service-logo"
          >
            <ServiceIcon size={18} />
          </div>

          <div
            aria-hidden="true"
            className="h-px w-6 shrink-0 bg-white/[0.06]"
          />
        </>
      )}

      <div
        className={cn(
          'no-scrollbar flex min-h-0 flex-col items-center overflow-y-auto',
          isStudioVariant ? 'flex-1 gap-1' : 'flex-1 gap-2.5'
        )}
        data-testid="studio-primary-navigation"
      >
        {globalActions.length > 0 ? (
          <div
            className={cn(
              'flex flex-col items-center',
              isStudioVariant ? 'gap-1' : 'gap-2.5'
            )}
          >
            {globalActions.map(action => renderActionButton(action, 'global'))}
          </div>
        ) : (
          <div
            className={cn(
              'flex flex-col items-center',
              isStudioVariant ? 'gap-1' : 'gap-2.5'
            )}
          >
            {modes.map(renderModeButton)}
          </div>
        )}
      </div>

      <div
        className={cn(
          'no-scrollbar flex w-full shrink-0 flex-col items-center overflow-y-auto border-t',
          isStudioVariant
            ? 'gap-1 border-transparent pt-0'
            : 'mt-auto gap-2.5 border-white/5 pt-2.5'
        )}
        data-testid="studio-utility-bar"
      >
        {utilityActions.map(action => renderActionButton(action, 'utility'))}
      </div>

      {isStudioVariant && environmentStatus && (
        <div
          className="mx-2 mb-1 mt-auto shrink-0 rounded-md border border-white/10 bg-[#0b1120] px-2 py-2"
          data-testid="studio-environment-status"
          style={{
            background: '#091524',
            borderColor: '#23364b',
            boxShadow: 'inset 0 1px 0 rgba(148, 190, 230, 0.05)',
            width: 68,
          }}
          title={`${environmentStatus.label} · ${environmentStatus.detail}`}
        >
          <div
            className={cn(
              'flex items-center gap-1.5 font-mono text-[10px] font-bold',
              environmentStatus.tone === 'ready'
                ? 'text-emerald-300'
                : environmentStatus.tone === 'reduce-only'
                  ? 'text-sky-300'
                  : environmentStatus.tone === 'checking'
                    ? 'text-amber-300'
                    : 'text-red-300'
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                environmentStatus.tone === 'ready'
                  ? 'bg-emerald-400'
                  : environmentStatus.tone === 'reduce-only'
                    ? 'bg-sky-400'
                    : environmentStatus.tone === 'checking'
                      ? 'bg-amber-400'
                      : 'bg-red-400'
              )}
            />
            {environmentStatus.label}
          </div>
          <div className="mt-1 truncate text-center text-[10px] text-slate-500">
            {environmentStatus.detail}
          </div>
        </div>
      )}
    </aside>
  );
}

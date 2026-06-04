import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import type React from 'react';
import { createPortal } from 'react-dom';

import { useStudioWorkspaceContext } from '@/components/studio-workspace/context';
import { cn } from '@/utils/cn';

import { ActivityBar } from './ActivityBar';
import {
  DEFAULT_SIDEBAR_WIDTH,
  MAX_SIDEBAR_WIDTH,
  MIN_SIDEBAR_WIDTH,
  RESIZE_LARGE_STEP,
  RESIZE_STEP,
  clampSidebarWidth,
  readStudioSidebarWidths,
  writeStudioSidebarWidths,
} from './sidebarSizing';
import { StatusBar } from './StatusBar';
import { getStudioThemeStyles } from './themeStyles';
import type {
  StudioAction,
  StudioMode,
  StudioTheme,
  StudioWorkbenchProps,
} from './types';
import { usePageStudioStatusBar } from './usePageStudioStatusBar';
import { useStudioGlobalActions } from './useStudioGlobalActions';

interface StudioStandaloneWorkbenchProps {
  activeMode: string;
  children: React.ReactNode;
  className?: string;
  globalActions?: StudioAction[];
  isPage: boolean;
  modes: StudioMode[];
  onExit?: () => void;
  onModeChange: (mode: string) => void;
  statusBarLeft?: React.ReactNode;
  statusBarRight?: React.ReactNode;
  theme: StudioTheme;
  utilityActions?: StudioAction[];
  usesGlobalStatusBar: boolean;
}

function StudioStandaloneWorkbench({
  activeMode,
  children,
  className,
  globalActions,
  isPage,
  modes,
  onExit,
  onModeChange,
  statusBarLeft,
  statusBarRight,
  theme,
  utilityActions,
  usesGlobalStatusBar,
}: StudioStandaloneWorkbenchProps) {
  const fallbackActions = useStudioGlobalActions();
  const resolvedGlobalActions = globalActions ?? fallbackActions.globalActions;
  const resolvedUtilityActions =
    utilityActions ?? fallbackActions.utilityActions;

  return (
    <div
      data-studio-workbench
      className={cn(
        'studio-workbench flex flex-col bg-[var(--studio-bg)] text-slate-200 font-sans',
        isPage
          ? 'h-full min-h-0 w-full'
          : 'fixed inset-0 z-[9999] animate-fade-in',
        className
      )}
    >
      <div className="flex min-h-0 flex-1">
        <ActivityBar
          activeMode={activeMode}
          globalActions={resolvedGlobalActions}
          modes={modes}
          onExit={onExit}
          onModeChange={onModeChange}
          theme={theme}
          utilityActions={resolvedUtilityActions}
        />
        {children}
      </div>

      {!usesGlobalStatusBar && (
        <StatusBar left={statusBarLeft} right={statusBarRight} />
      )}
    </div>
  );
}

export function StudioWorkbench({
  activeMode,
  className,
  content,
  emptyState,
  globalActions,
  isEmpty = false,
  isPage = false,
  modes,
  onExit,
  onModeChange,
  showSidebar = true,
  sidebar,
  sidebarSizing,
  statusBarLeft,
  statusBarRight,
  tabBar,
  theme,
  utilityActions,
}: StudioWorkbenchProps) {
  const workspace = useStudioWorkspaceContext();
  const isHostedPage = isPage && Boolean(workspace?.isWorkspaceHosted);
  const clearWorkspaceSidebar = workspace?.clearWorkspaceSidebar;
  const setWorkspaceSidebar = workspace?.setWorkspaceSidebar;
  const hostedSidebarOwnerId = useId();
  const usesGlobalStatusBar = usePageStudioStatusBar({
    enabled: isPage && !isHostedPage,
    left: statusBarLeft,
    right: statusBarRight,
  });
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [sidebarWidths, setSidebarWidths] =
    useState<Record<string, number>>(readStudioSidebarWidths);
  const resizeStateRef = useRef({
    startWidth: DEFAULT_SIDEBAR_WIDTH,
    startX: 0,
  });
  const themeStyles = getStudioThemeStyles(theme.name);

  const minSidebarWidth = sidebarSizing?.minWidth ?? MIN_SIDEBAR_WIDTH;
  const maxSidebarWidth = Math.max(
    minSidebarWidth,
    sidebarSizing?.maxWidth ?? MAX_SIDEBAR_WIDTH
  );
  const defaultSidebarWidth = clampSidebarWidth(
    sidebarSizing?.defaultWidth ?? DEFAULT_SIDEBAR_WIDTH,
    minSidebarWidth,
    maxSidebarWidth
  );
  const sidebarWidthKey = useMemo(() => {
    return sidebarSizing?.storageScope || theme.title || 'studio-workbench';
  }, [sidebarSizing?.storageScope, theme.title]);
  const sidebarWidth = clampSidebarWidth(
    sidebarWidths[sidebarWidthKey] ?? defaultSidebarWidth,
    minSidebarWidth,
    maxSidebarWidth
  );
  const effectiveTabBar = isHostedPage ? null : tabBar;

  const updateSidebarWidth = useCallback(
    (nextWidth: number) => {
      setSidebarWidths(current => {
        const next = {
          ...current,
          [sidebarWidthKey]: clampSidebarWidth(
            nextWidth,
            minSidebarWidth,
            maxSidebarWidth
          ),
        };
        writeStudioSidebarWidths(next);
        return next;
      });
    },
    [maxSidebarWidth, minSidebarWidth, sidebarWidthKey]
  );

  const handleSidebarResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      resizeStateRef.current = {
        startWidth: sidebarWidth,
        startX: event.clientX,
      };
      setIsResizingSidebar(true);
    },
    [sidebarWidth]
  );

  const handleSidebarResizeKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? RESIZE_LARGE_STEP : RESIZE_STEP;
      let nextWidth: number | null = null;

      if (event.key === 'ArrowLeft') {
        nextWidth = sidebarWidth - step;
      } else if (event.key === 'ArrowRight') {
        nextWidth = sidebarWidth + step;
      } else if (event.key === 'Home') {
        nextWidth = minSidebarWidth;
      } else if (event.key === 'End') {
        nextWidth = maxSidebarWidth;
      }

      if (nextWidth === null) return;
      event.preventDefault();
      updateSidebarWidth(nextWidth);
    },
    [maxSidebarWidth, minSidebarWidth, sidebarWidth, updateSidebarWidth]
  );

  useEffect(() => {
    if (!isResizingSidebar) return;

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handlePointerMove = (event: PointerEvent) => {
      const deltaX = event.clientX - resizeStateRef.current.startX;
      updateSidebarWidth(resizeStateRef.current.startWidth + deltaX);
    };

    const handlePointerUp = () => {
      setIsResizingSidebar(false);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizingSidebar, updateSidebarWidth]);

  useEffect(() => {
    if (!isHostedPage) return;

    if (showSidebar && sidebar) {
      setWorkspaceSidebar?.({
        content: sidebar,
        ownerId: hostedSidebarOwnerId,
        showSidebar: true,
        sizing: sidebarSizing,
        themeName: theme.name,
        title: theme.title,
      });
    } else {
      clearWorkspaceSidebar?.(hostedSidebarOwnerId);
    }
  }, [
    clearWorkspaceSidebar,
    hostedSidebarOwnerId,
    isHostedPage,
    setWorkspaceSidebar,
    showSidebar,
    sidebar,
    sidebarSizing,
    theme.name,
    theme.title,
  ]);

  useEffect(() => {
    if (!isHostedPage) return;

    return () => {
      clearWorkspaceSidebar?.(hostedSidebarOwnerId);
    };
  }, [clearWorkspaceSidebar, hostedSidebarOwnerId, isHostedPage]);

  const renderLocalFrame = () => (
    <div
      className={cn(
        'flex min-h-0 flex-1',
        isHostedPage && 'h-full w-full',
        isHostedPage && className
      )}
      data-testid="studio-local-frame"
    >
      {!isHostedPage && showSidebar && sidebar && (
        <div
          className={cn(
            'relative flex h-full shrink-0 flex-col border-r border-white/5 bg-[#0b1120]/50',
            !isResizingSidebar && 'transition-[width] duration-150'
          )}
          style={{ width: sidebarWidth }}
        >
          {sidebar}
          <div
            role="separator"
            aria-label={`${theme.title} 侧边栏宽度`}
            aria-orientation="vertical"
            aria-valuemin={minSidebarWidth}
            aria-valuemax={maxSidebarWidth}
            aria-valuenow={sidebarWidth}
            data-testid="studio-sidebar-resizer"
            tabIndex={0}
            onPointerDown={handleSidebarResizeStart}
            onKeyDown={handleSidebarResizeKeyDown}
            className={cn(
              'group absolute -right-1.5 top-0 z-30 h-full w-3 cursor-col-resize touch-none outline-none',
              isResizingSidebar && themeStyles.resizeOverlay
            )}
          >
            <div
              className={cn(
                'absolute left-1/2 top-0 h-full w-px -translate-x-1/2 transition-colors',
                isResizingSidebar
                  ? themeStyles.resizeLine
                  : `bg-white/5 ${themeStyles.resizeLineHover}`
              )}
            />
            <div
              className={cn(
                'absolute left-1/2 top-1/2 h-12 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all',
                isResizingSidebar
                  ? `${themeStyles.resizeHandle} opacity-100`
                  : 'bg-slate-500/60 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'
              )}
            />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col bg-[#0b1120]/20">
        {effectiveTabBar && !isEmpty && effectiveTabBar}
        {isEmpty && emptyState ? (
          emptyState
        ) : (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {content}
          </div>
        )}
      </div>
    </div>
  );

  if (isHostedPage) return renderLocalFrame();

  const renderWorkbench = () => (
    <StudioStandaloneWorkbench
      activeMode={activeMode}
      className={className}
      globalActions={globalActions}
      isPage={isPage}
      modes={modes}
      onExit={onExit}
      onModeChange={onModeChange}
      statusBarLeft={statusBarLeft}
      statusBarRight={statusBarRight}
      theme={theme}
      utilityActions={utilityActions}
      usesGlobalStatusBar={usesGlobalStatusBar}
    >
      {renderLocalFrame()}
    </StudioStandaloneWorkbench>
  );

  if (isPage) return renderWorkbench();
  if (typeof document === 'undefined') return null;
  return createPortal(renderWorkbench(), document.body);
}

import { Pin, Plus, X } from 'lucide-react';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type React from 'react';

import { cn } from '@/utils/cn';

import {
  STUDIO_HEADER_HEIGHT,
  STUDIO_WORKSPACE_ACTIVE_TAB_STYLE,
  STUDIO_WORKSPACE_SURFACE,
  STUDIO_WORKSPACE_TAB_RADIUS,
  STUDIO_WORKSPACE_TAB_STYLE,
  STUDIO_WORKSPACE_WEAK_BORDER,
} from './studioShellStyles';
import {
  StudioTabContextMenu,
  type StudioTabContextMenuAction,
  type StudioTabContextMenuState,
} from './StudioTabContextMenu';
import { getStudioThemeStyles } from './themeStyles';
import type { StudioTab, StudioThemeName } from './types';

const WORKSPACE_TAB_SHOULDER_SIZE = STUDIO_WORKSPACE_TAB_RADIUS + 1;
const WORKSPACE_TAB_SHOULDER_KAPPA = 0.55228475;
const workspaceTabShoulderFillControl = Number(
  (WORKSPACE_TAB_SHOULDER_SIZE * WORKSPACE_TAB_SHOULDER_KAPPA).toFixed(2)
);
const workspaceTabShoulderStrokeRadius = WORKSPACE_TAB_SHOULDER_SIZE - 0.5;
const workspaceTabShoulderStrokeControl = Number(
  (workspaceTabShoulderStrokeRadius * WORKSPACE_TAB_SHOULDER_KAPPA).toFixed(2)
);
const WORKSPACE_TAB_SHOULDER_PATHS = {
  leftFill: `M${WORKSPACE_TAB_SHOULDER_SIZE} 0 C${WORKSPACE_TAB_SHOULDER_SIZE} ${workspaceTabShoulderFillControl} ${workspaceTabShoulderFillControl} ${WORKSPACE_TAB_SHOULDER_SIZE} 0 ${WORKSPACE_TAB_SHOULDER_SIZE} H${WORKSPACE_TAB_SHOULDER_SIZE} Z`,
  leftStroke: `M${workspaceTabShoulderStrokeRadius} 0 C${workspaceTabShoulderStrokeRadius} ${workspaceTabShoulderStrokeControl} ${workspaceTabShoulderStrokeControl} ${workspaceTabShoulderStrokeRadius} 0 ${workspaceTabShoulderStrokeRadius}`,
  rightFill: `M0 0 C0 ${workspaceTabShoulderFillControl} ${WORKSPACE_TAB_SHOULDER_SIZE - workspaceTabShoulderFillControl} ${WORKSPACE_TAB_SHOULDER_SIZE} ${WORKSPACE_TAB_SHOULDER_SIZE} ${WORKSPACE_TAB_SHOULDER_SIZE} H0 Z`,
  rightStroke: `M0.5 0 C0.5 ${workspaceTabShoulderStrokeControl} ${WORKSPACE_TAB_SHOULDER_SIZE - workspaceTabShoulderStrokeControl} ${workspaceTabShoulderStrokeRadius} ${WORKSPACE_TAB_SHOULDER_SIZE} ${workspaceTabShoulderStrokeRadius}`,
} as const;

export interface TabBarProps<T extends StudioTab> {
  activeTabId: string | null;
  canCloseTab?: (tab: T) => boolean;
  closable?: boolean;
  createTooltip?: string;
  onTabChange: (tabId: string) => void;
  onTabClose: (tabId: string, event?: React.MouseEvent) => void;
  onTabCreate?: () => void;
  onTabPin?: (tabId: string, pinned: boolean) => void;
  renderTabContent?: (tab: T, isActive: boolean) => React.ReactNode;
  tabs: T[];
  themeColor: StudioThemeName;
  variant?: 'default' | 'workspace';
}

export function TabBar<T extends StudioTab>({
  activeTabId,
  canCloseTab,
  closable = true,
  createTooltip = 'New Tab',
  onTabChange,
  onTabClose,
  onTabCreate,
  onTabPin,
  renderTabContent,
  tabs,
  themeColor,
  variant = 'default',
}: TabBarProps<T>) {
  const [contextMenu, setContextMenu] =
    useState<StudioTabContextMenuState | null>(null);
  const tabButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const tabListRef = useRef<HTMLDivElement>(null);
  const themeStyles = getStudioThemeStyles(themeColor);
  const isWorkspaceVariant = variant === 'workspace';

  useLayoutEffect(() => {
    if (!activeTabId) return;

    const revealActiveTab = () => {
      const tabList = tabListRef.current;
      const activeTab = tabButtonRefs.current.get(activeTabId)?.parentElement;
      if (!tabList || !activeTab) return;
      if (activeTab.dataset.studioFixedTab === 'true') return;

      const padding = isWorkspaceVariant ? STUDIO_WORKSPACE_TAB_RADIUS + 4 : 8;
      const visibleStart = tabList.scrollLeft;
      const visibleEnd = tabList.scrollLeft + tabList.clientWidth;
      const tabStart = activeTab.offsetLeft;
      const tabEnd = tabStart + activeTab.offsetWidth;

      if (tabStart < visibleStart) {
        tabList.scrollLeft = Math.max(0, tabStart - padding);
      } else if (tabEnd > visibleEnd) {
        tabList.scrollLeft = Math.max(
          0,
          tabEnd - tabList.clientWidth + padding
        );
      }
    };

    revealActiveTab();
    const deferredReveal = window.setTimeout(revealActiveTab, 0);
    const resizeObserver =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(revealActiveTab);
    if (tabListRef.current) resizeObserver?.observe(tabListRef.current);
    window.addEventListener('resize', revealActiveTab);
    return () => {
      window.clearTimeout(deferredReveal);
      resizeObserver?.disconnect();
      window.removeEventListener('resize', revealActiveTab);
    };
  }, [activeTabId, isWorkspaceVariant, tabs.length]);

  const handleContextMenuAction = useCallback(
    (action: StudioTabContextMenuAction, tabId: string) => {
      const tabIndex = tabs.findIndex(tab => tab.id === tabId);
      if (tabIndex === -1) return;

      if (action === 'pin' || action === 'unpin') {
        onTabPin?.(tabId, action === 'pin');
        return;
      }

      if (action === 'close') {
        const tab = tabs[tabIndex];
        if (closable && (canCloseTab?.(tab) ?? true)) onTabClose(tabId);
        return;
      }

      if (action === 'closeOthers') {
        onTabChange(tabId);
        tabs
          .filter(
            tab => tab.id !== tabId && closable && (canCloseTab?.(tab) ?? true)
          )
          .forEach(tab => onTabClose(tab.id));
        return;
      }

      if (action === 'closeRight') {
        onTabChange(tabId);
        tabs
          .slice(tabIndex + 1)
          .filter(tab => closable && (canCloseTab?.(tab) ?? true))
          .forEach(tab => onTabClose(tab.id));
        return;
      }

      tabs
        .filter(tab => closable && (canCloseTab?.(tab) ?? true))
        .forEach(tab => onTabClose(tab.id));
    },
    [canCloseTab, closable, onTabChange, onTabClose, onTabPin, tabs]
  );

  const handleTabKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, tabId: string) => {
      const currentIndex = tabs.findIndex(tab => tab.id === tabId);
      if (currentIndex === -1) return;
      const currentTab = tabs[currentIndex];

      if (
        event.key === 'Delete' &&
        closable &&
        (canCloseTab?.(currentTab) ?? true)
      ) {
        event.preventDefault();
        onTabClose(tabId);
        return;
      }

      let nextIndex: number | null = null;
      if (event.key === 'ArrowLeft') {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      } else if (event.key === 'ArrowRight') {
        nextIndex = (currentIndex + 1) % tabs.length;
      } else if (event.key === 'Home') {
        nextIndex = 0;
      } else if (event.key === 'End') {
        nextIndex = tabs.length - 1;
      }

      if (nextIndex === null) return;

      event.preventDefault();
      const nextTab = tabs[nextIndex];
      onTabChange(nextTab.id);
      window.requestAnimationFrame(() => {
        tabButtonRefs.current.get(nextTab.id)?.focus();
      });
    },
    [canCloseTab, closable, onTabChange, onTabClose, tabs]
  );

  const isWorkspaceTabFixed = (tab: T) =>
    isWorkspaceVariant &&
    closable &&
    Boolean(canCloseTab) &&
    !(canCloseTab?.(tab) ?? true);
  const fixedWorkspaceTabs = tabs.filter(isWorkspaceTabFixed);
  const tabGroups =
    isWorkspaceVariant && fixedWorkspaceTabs.length > 0
      ? [
          { id: 'fixed', isFixed: true, tabs: fixedWorkspaceTabs },
          {
            id: 'scrolling',
            isFixed: false,
            tabs: tabs.filter(tab => !isWorkspaceTabFixed(tab)),
          },
        ]
      : [{ id: 'scrolling', isFixed: false, tabs }];

  return (
    <div
      className={cn(
        'relative shrink-0 bg-[#07111f]',
        isWorkspaceVariant
          ? 'studio-shell-tabbar overflow-visible bg-transparent'
          : 'overflow-hidden'
      )}
      data-variant={variant}
      data-testid="studio-tab-bar"
      style={
        isWorkspaceVariant
          ? {
              background:
                'linear-gradient(180deg, rgba(4, 12, 23, 0.1), rgba(4, 11, 21, 0.54))',
              height: '100%',
            }
          : { height: STUDIO_HEADER_HEIGHT }
      }
    >
      <div
        ref={isWorkspaceVariant ? undefined : tabListRef}
        aria-label="工作区标签"
        className={cn(
          'flex h-full min-w-0 items-end gap-0.5',
          isWorkspaceVariant
            ? 'overflow-hidden px-4'
            : 'no-scrollbar overflow-x-auto overscroll-x-contain scroll-smooth px-1.5'
        )}
        role="tablist"
        style={
          isWorkspaceVariant
            ? {
                height: 'calc(100% + 1px)',
                marginBottom: -1,
                paddingBottom: 1,
              }
            : undefined
        }
      >
        {tabGroups.map(group => (
          <div
            key={group.id}
            ref={isWorkspaceVariant && !group.isFixed ? tabListRef : undefined}
            className={cn(
              isWorkspaceVariant
                ? group.isFixed
                  ? 'relative z-20 flex h-full shrink-0 items-end'
                  : 'no-scrollbar flex h-full min-w-0 flex-1 items-end gap-0.5 overflow-x-auto overscroll-x-contain scroll-auto'
                : 'contents'
            )}
            style={
              isWorkspaceVariant && !group.isFixed
                ? {
                    height: 'calc(100% + 1px)',
                    marginBottom: -1,
                    paddingBottom: 1,
                  }
                : undefined
            }
            data-testid={
              isWorkspaceVariant
                ? group.isFixed
                  ? 'studio-fixed-tab-region'
                  : 'studio-scrollable-tab-region'
                : undefined
            }
          >
            {group.tabs.map(tab => {
              const isActive = activeTabId === tab.id;
              const isTabClosable = closable && (canCloseTab?.(tab) ?? true);
              const isFixedWorkspaceTab = group.isFixed;

              return (
                <div
                  key={tab.id}
                  data-studio-fixed-tab={
                    isFixedWorkspaceTab ? 'true' : undefined
                  }
                  onContextMenu={event => {
                    if (!closable) return;
                    event.preventDefault();
                    event.stopPropagation();
                    const tabIndex = tabs.findIndex(item => item.id === tab.id);
                    const closableTabCount = tabs.filter(
                      item => closable && (canCloseTab?.(item) ?? true)
                    ).length;
                    const closableTabsRight = tabs
                      .slice(tabIndex + 1)
                      .filter(
                        item => closable && (canCloseTab?.(item) ?? true)
                      ).length;
                    setContextMenu({
                      canClose: isTabClosable,
                      closableTabCount,
                      closableTabsRight,
                      isPreview: Boolean(tab.isPreview),
                      isPreviewable: Boolean(tab.isPreviewable),
                      tabId: tab.id,
                      tabIndex,
                      x: event.clientX,
                      y: event.clientY,
                    });
                  }}
                  className={cn(
                    'group relative flex h-12 shrink-0 items-center border border-b-0 transition-colors duration-150',
                    isActive
                      ? cn(
                          themeStyles.activeTab,
                          isWorkspaceVariant && 'text-slate-100'
                        )
                      : isWorkspaceVariant
                        ? 'border-transparent bg-transparent text-slate-500 hover:bg-white/5 hover:text-slate-200'
                        : 'border-transparent bg-transparent text-slate-500 hover:border-white/5 hover:bg-white/5 hover:text-slate-200'
                  )}
                  style={{
                    ...(isWorkspaceVariant
                      ? {
                          ...STUDIO_WORKSPACE_TAB_STYLE,
                          maxWidth: 'min(13rem, 100%)',
                          minWidth: '6.5rem',
                          width: 'fit-content',
                        }
                      : {
                          width: 208,
                          ...(isActive
                            ? {
                                borderTopLeftRadius:
                                  'calc(var(--radius) - 2px)',
                                borderTopRightRadius:
                                  'calc(var(--radius) - 2px)',
                                zIndex: 10,
                              }
                            : {}),
                        }),
                    ...(isActive && isWorkspaceVariant
                      ? STUDIO_WORKSPACE_ACTIVE_TAB_STYLE
                      : {}),
                  }}
                >
                  <button
                    ref={node => {
                      if (node) tabButtonRefs.current.set(tab.id, node);
                      else tabButtonRefs.current.delete(tab.id);
                    }}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    tabIndex={isActive ? 0 : -1}
                    data-studio-tab-id={tab.id}
                    onClick={() => onTabChange(tab.id)}
                    onDoubleClick={() => {
                      if (tab.isPreview) onTabPin?.(tab.id, true);
                    }}
                    onKeyDown={event => handleTabKeyDown(event, tab.id)}
                    title={
                      isWorkspaceVariant
                        ? undefined
                        : tab.isPreview
                          ? `${tab.name}（预览标签，双击固定）`
                          : tab.name
                    }
                    className={cn(
                      'flex h-full min-w-0 flex-1 cursor-pointer items-center gap-2 pl-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
                      themeStyles.focusRing,
                      isWorkspaceVariant && 'pl-3.5'
                    )}
                  >
                    <span className="flex min-w-0 flex-1 items-center gap-2">
                      {renderTabContent ? (
                        renderTabContent(tab, isActive)
                      ) : (
                        <>
                          {tab.icon && (
                            <tab.icon
                              size={13}
                              className={themeStyles.tabIcon}
                            />
                          )}
                          <span
                            className={cn(
                              'truncate text-xs font-bold',
                              tab.isPreview && 'italic'
                            )}
                          >
                            {tab.name}
                          </span>
                        </>
                      )}
                    </span>
                    {tab.isPreviewable && !tab.isPreview && (
                      <Pin
                        aria-label="已固定"
                        className="h-3 w-3 shrink-0 text-slate-500"
                      />
                    )}
                  </button>

                  {isTabClosable && (
                    <div className="relative mr-1 flex h-6 w-6 shrink-0 items-center justify-center">
                      {tab.isDirty && (
                        <span className="pointer-events-none absolute inset-0 flex items-center justify-center transition-opacity group-hover:opacity-0 group-focus-within:opacity-0">
                          <span className="relative flex h-2 w-2">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-75" />
                            <span className="relative inline-flex h-2 w-2 rounded-full bg-orange-500" />
                          </span>
                        </span>
                      )}

                      <button
                        type="button"
                        onClick={event => onTabClose(tab.id, event)}
                        className={cn(
                          'rounded p-1 text-slate-500 opacity-0 transition-colors hover:bg-white/10 hover:text-white focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 group-hover:opacity-100 group-focus-within:opacity-100',
                          themeStyles.focusRing,
                          isActive && 'opacity-100'
                        )}
                        aria-label={`关闭 ${tab.name}`}
                      >
                        <X size={13} />
                      </button>
                    </div>
                  )}

                  {isActive && isWorkspaceVariant && (
                    <>
                      <svg
                        aria-hidden="true"
                        data-testid="studio-workspace-tab-shoulder-left"
                        focusable="false"
                        shapeRendering="geometricPrecision"
                        viewBox={`0 0 ${WORKSPACE_TAB_SHOULDER_SIZE} ${WORKSPACE_TAB_SHOULDER_SIZE}`}
                        style={{
                          bottom: -1,
                          display: 'block',
                          height: WORKSPACE_TAB_SHOULDER_SIZE,
                          left: -WORKSPACE_TAB_SHOULDER_SIZE,
                          pointerEvents: 'none',
                          position: 'absolute',
                          width: WORKSPACE_TAB_SHOULDER_SIZE,
                          zIndex: 11,
                        }}
                      >
                        <path
                          d={WORKSPACE_TAB_SHOULDER_PATHS.leftFill}
                          fill={STUDIO_WORKSPACE_SURFACE}
                        />
                        <path
                          d={WORKSPACE_TAB_SHOULDER_PATHS.leftStroke}
                          fill="none"
                          stroke={STUDIO_WORKSPACE_WEAK_BORDER}
                          strokeWidth="1"
                          vectorEffect="non-scaling-stroke"
                        />
                      </svg>
                      <svg
                        aria-hidden="true"
                        data-testid="studio-workspace-tab-shoulder-right"
                        focusable="false"
                        shapeRendering="geometricPrecision"
                        viewBox={`0 0 ${WORKSPACE_TAB_SHOULDER_SIZE} ${WORKSPACE_TAB_SHOULDER_SIZE}`}
                        style={{
                          bottom: -1,
                          display: 'block',
                          height: WORKSPACE_TAB_SHOULDER_SIZE,
                          pointerEvents: 'none',
                          position: 'absolute',
                          right: -WORKSPACE_TAB_SHOULDER_SIZE,
                          width: WORKSPACE_TAB_SHOULDER_SIZE,
                          zIndex: 11,
                        }}
                      >
                        <path
                          d={WORKSPACE_TAB_SHOULDER_PATHS.rightFill}
                          fill={STUDIO_WORKSPACE_SURFACE}
                        />
                        <path
                          d={WORKSPACE_TAB_SHOULDER_PATHS.rightStroke}
                          fill="none"
                          stroke={STUDIO_WORKSPACE_WEAK_BORDER}
                          strokeWidth="1"
                          vectorEffect="non-scaling-stroke"
                        />
                      </svg>
                    </>
                  )}

                  {isActive && (
                    <span
                      aria-hidden="true"
                      data-testid={
                        isWorkspaceVariant
                          ? 'studio-workspace-tab-connector'
                          : undefined
                      }
                      style={{
                        background: isWorkspaceVariant
                          ? STUDIO_WORKSPACE_SURFACE
                          : '#0d1b2c',
                        bottom: -1,
                        height: 1,
                        left: 0,
                        pointerEvents: 'none',
                        position: 'absolute',
                        right: 0,
                      }}
                    />
                  )}
                </div>
              );
            })}

            {!group.isFixed && onTabCreate && (
              <button
                type="button"
                onClick={onTabCreate}
                className={cn(
                  'mb-1 flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
                  themeStyles.focusRing,
                  isWorkspaceVariant && 'mb-1.5 h-9 w-9'
                )}
                title={createTooltip}
                aria-label={createTooltip}
              >
                <Plus size={16} strokeWidth={2} />
              </button>
            )}
          </div>
        ))}
      </div>

      <StudioTabContextMenu
        menu={closable ? contextMenu : null}
        onAction={handleContextMenuAction}
        onClose={() => setContextMenu(null)}
      />
    </div>
  );
}

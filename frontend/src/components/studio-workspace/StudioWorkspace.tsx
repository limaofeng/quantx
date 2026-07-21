import { BarChart3, TrendingUp } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type React from 'react';
import { useLocation } from 'wouter';

import {
  ActivityBar,
  StatusBar,
  TabBar,
  type StudioMode,
  type StudioTheme,
  useStudioGlobalActions,
} from '@/components/studio-workbench';
import {
  STUDIO_WORKSPACE_SIDEBAR_SIZING,
  STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE,
  useStudioSidebarSizing,
} from '@/components/studio-workbench/sidebarSizing';
import { getStudioThemeStyles } from '@/components/studio-workbench/themeStyles';
import { cn } from '@/utils/cn';

import {
  StudioWorkspaceContext,
  type StudioWorkspaceSidebarConfig,
} from './context';
import {
  buildStudioWorkspaceTab,
  getStudioWorkspacePath,
  mergeStudioWorkspaceTab,
  normalizeStudioWorkspaceTabs,
  normalizeStudioWorkspaceTabTitles,
  type StudioWorkspaceTab,
} from './workspaceTabs';

const STORAGE_KEY = 'quantx-studio-workspace-tabs';
const DEFAULT_WORKSPACE_PATH = '/';
const studioWorkspaceModes: StudioMode[] = [];
const studioWorkspaceTheme: StudioTheme = {
  icon: TrendingUp,
  name: 'red',
  title: 'QuantX Studio',
};

function StudioWorkspaceStatusBar() {
  return (
    <StatusBar
      left={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            QuantX Studio
          </span>
          <span className="text-slate-700">|</span>
          <span>全局工作台</span>
        </>
      }
      right={
        <>
          <span>全局导航</span>
          <span className="text-slate-700">|</span>
          <span>资产 / 主题 / 通知</span>
        </>
      }
    />
  );
}

function StudioWorkspaceSidebarDock({
  sidebar,
}: {
  sidebar: StudioWorkspaceSidebarConfig | null;
}) {
  const {
    handleSidebarResizeKeyDown,
    handleSidebarResizeStart,
    isResizingSidebar,
    maxSidebarWidth,
    minSidebarWidth,
    sidebarWidth,
  } = useStudioSidebarSizing({
    resizeEdge: 'right',
    sizing: STUDIO_WORKSPACE_SIDEBAR_SIZING,
    storageFallback: STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE,
  });
  const themeStyles = getStudioThemeStyles(sidebar?.themeName ?? 'red');

  if (!sidebar?.showSidebar || !sidebar.content) return null;

  return (
    <aside
      data-testid="studio-sidebar-dock"
      className={cn(
        'relative flex h-full min-h-0 shrink-0 flex-col border-r border-white/5 bg-[#0b1120]/70',
        !isResizingSidebar && 'transition-[width] duration-150'
      )}
      style={{ width: sidebarWidth }}
    >
      <div
        role="separator"
        aria-label={`${sidebar.title} 侧边栏宽度`}
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
      {sidebar.content}
    </aside>
  );
}

function readWorkspaceTabs(): StudioWorkspaceTab[] {
  if (typeof window === 'undefined') return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Array<Partial<StudioWorkspaceTab>>;
    if (!Array.isArray(parsed)) return [];

    const restoredTabs = parsed
      .map(item =>
        typeof item.path === 'string'
          ? buildStudioWorkspaceTab(item.path)
          : null
      )
      .filter((item): item is StudioWorkspaceTab => Boolean(item));

    return normalizeStudioWorkspaceTabs(restoredTabs);
  } catch {
    return [];
  }
}

function areTabsEqual(left: StudioWorkspaceTab[], right: StudioWorkspaceTab[]) {
  if (left.length !== right.length) return false;
  return left.every((tab, index) => {
    const other = right[index];
    return (
      tab.icon === other.icon &&
      tab.id === other.id &&
      tab.name === other.name &&
      tab.path === other.path &&
      tab.type === other.type
    );
  });
}

function areSidebarSizingEqual(
  left?: StudioWorkspaceSidebarConfig['sizing'],
  right?: StudioWorkspaceSidebarConfig['sizing']
) {
  return (
    left?.defaultWidth === right?.defaultWidth &&
    left?.maxWidth === right?.maxWidth &&
    left?.minWidth === right?.minWidth &&
    left?.storageScope === right?.storageScope
  );
}

function areSidebarConfigsEqual(
  left: StudioWorkspaceSidebarConfig | null,
  right: StudioWorkspaceSidebarConfig
) {
  return (
    left?.content === right.content &&
    left?.ownerId === right.ownerId &&
    left?.showSidebar === right.showSidebar &&
    left?.themeName === right.themeName &&
    left?.title === right.title &&
    areSidebarSizingEqual(left?.sizing, right.sizing)
  );
}

function writeWorkspaceTabs(tabs: StudioWorkspaceTab[]) {
  if (typeof window === 'undefined') return;

  try {
    const normalizedTabs = normalizeStudioWorkspaceTabs(tabs);
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(normalizedTabs.map(({ id, path }) => ({ id, path })))
    );
  } catch {
    // localStorage may be unavailable in hardened browser contexts.
  }
}

function getFallbackTab() {
  return buildStudioWorkspaceTab(DEFAULT_WORKSPACE_PATH);
}

export function StudioWorkspace({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const { globalActions, utilityActions } = useStudioGlobalActions();
  const currentPath = getStudioWorkspacePath(location);
  const initialTab = buildStudioWorkspaceTab(currentPath) || getFallbackTab();
  const [tabs, setTabs] = useState<StudioWorkspaceTab[]>(() => {
    const restoredTabs = readWorkspaceTabs();
    if (!initialTab) return restoredTabs;
    return mergeStudioWorkspaceTab(restoredTabs, initialTab);
  });
  const [activeTabId, setActiveTabId] = useState<string | null>(
    initialTab?.id || null
  );
  const [workspaceSidebar, setWorkspaceSidebarState] =
    useState<StudioWorkspaceSidebarConfig | null>(null);

  useEffect(() => {
    const currentTab = buildStudioWorkspaceTab(currentPath);
    if (!currentTab) return;

    setTabs(currentTabs =>
      normalizeStudioWorkspaceTabs(
        mergeStudioWorkspaceTab(currentTabs, currentTab)
      )
    );
    setActiveTabId(currentTab.id);
  }, [currentPath]);

  useEffect(() => {
    setTabs(currentTabs => {
      const normalizedTabs = normalizeStudioWorkspaceTabs(currentTabs);
      return areTabsEqual(currentTabs, normalizedTabs)
        ? currentTabs
        : normalizedTabs;
    });
  }, [tabs]);

  useEffect(() => {
    writeWorkspaceTabs(tabs);
  }, [tabs]);

  const openStudioTab = useCallback(
    (path: string) => {
      const nextTab = buildStudioWorkspaceTab(path);
      if (!nextTab) {
        setLocation(path);
        return;
      }

      setTabs(currentTabs =>
        normalizeStudioWorkspaceTabs(
          mergeStudioWorkspaceTab(currentTabs, nextTab)
        )
      );
      setActiveTabId(nextTab.id);
      setLocation(nextTab.path);
    },
    [setLocation]
  );

  const updateActiveTab = useCallback(
    (patch: { name?: string }) => {
      if (!activeTabId) return;
      setTabs(currentTabs =>
        currentTabs.map(tab =>
          tab.id === activeTabId ? { ...tab, ...patch } : tab
        )
      );
    },
    [activeTabId]
  );

  const setWorkspaceSidebar = useCallback(
    (config: StudioWorkspaceSidebarConfig) => {
      setWorkspaceSidebarState(current =>
        areSidebarConfigsEqual(current, config) ? current : config
      );
    },
    []
  );

  const clearWorkspaceSidebar = useCallback((ownerId: string) => {
    setWorkspaceSidebarState(current =>
      current?.ownerId === ownerId ? null : current
    );
  }, []);

  const handleTabChange = useCallback(
    (tabId: string) => {
      const tab = tabs.find(item => item.id === tabId);
      if (!tab) return;

      setActiveTabId(tab.id);
      if (tab.path !== currentPath) setLocation(tab.path);
    },
    [currentPath, setLocation, tabs]
  );

  const handleTabClose = useCallback(
    (tabId: string, event?: React.MouseEvent) => {
      event?.stopPropagation();
      let nextActivePath: string | null = null;
      let nextActiveId: string | null = null;

      setTabs(currentTabs => {
        const tabIndex = currentTabs.findIndex(tab => tab.id === tabId);
        if (tabIndex === -1) return currentTabs;

        const nextTabs = currentTabs.filter(tab => tab.id !== tabId);

        if (nextTabs.length === 0) {
          const fallbackTab = getFallbackTab();
          if (!fallbackTab) return nextTabs;
          nextActiveId = fallbackTab.id;
          nextActivePath = fallbackTab.path;
          return [fallbackTab];
        }

        if (activeTabId === tabId) {
          const nextTab =
            nextTabs[Math.max(0, tabIndex - 1)] || nextTabs[0] || null;
          nextActiveId = nextTab?.id || null;
          nextActivePath = nextTab?.path || null;
        }

        return nextTabs;
      });

      if (nextActiveId) setActiveTabId(nextActiveId);
      if (nextActivePath) setLocation(nextActivePath);
    },
    [activeTabId, setLocation]
  );

  const workspaceTabBar = useMemo(() => {
    const displayTabs = normalizeStudioWorkspaceTabTitles(tabs);

    return (
      <TabBar
        activeTabId={activeTabId}
        onTabChange={handleTabChange}
        onTabClose={handleTabClose}
        renderTabContent={(tab: StudioWorkspaceTab, isActive) => {
          const Icon = tab.icon || BarChart3;
          return (
            <>
              <Icon
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  isActive ? 'text-red-400' : 'text-slate-500'
                )}
              />
              <span
                className={cn(
                  'max-w-[150px] truncate text-[11px] font-black',
                  tab.isPreview && 'italic'
                )}
              >
                {tab.name}
              </span>
            </>
          );
        }}
        tabs={displayTabs}
        themeColor="red"
      />
    );
  }, [activeTabId, handleTabChange, handleTabClose, tabs]);

  const contextValue = useMemo(
    () => ({
      activeTabId,
      clearWorkspaceSidebar,
      isWorkspaceHosted: true,
      openStudioTab,
      setWorkspaceSidebar,
      tabBar: workspaceTabBar,
      updateActiveTab,
    }),
    [
      activeTabId,
      clearWorkspaceSidebar,
      openStudioTab,
      setWorkspaceSidebar,
      updateActiveTab,
      workspaceTabBar,
    ]
  );

  return (
    <StudioWorkspaceContext.Provider value={contextValue}>
      <div
        data-studio-workbench
        className="studio-workbench flex h-full min-h-0 w-full flex-col bg-[var(--studio-bg)] text-slate-200 font-sans"
      >
        <div className="flex min-h-0 flex-1">
          <ActivityBar
            activeMode="WORKSPACE"
            globalActions={globalActions}
            modes={studioWorkspaceModes}
            onModeChange={() => undefined}
            theme={studioWorkspaceTheme}
            utilityActions={utilityActions}
          />

          <div className="flex min-w-0 flex-1">
            <StudioWorkspaceSidebarDock sidebar={workspaceSidebar} />
            <div
              className="flex min-w-0 flex-1 flex-col bg-[#0b1120]/20"
              data-testid="studio-workspace-main"
            >
              {workspaceTabBar}
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                {children}
              </div>
            </div>
          </div>
        </div>
        <StudioWorkspaceStatusBar />
      </div>
    </StudioWorkspaceContext.Provider>
  );
}

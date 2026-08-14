import { BarChart3, Bot, TrendingUp, UserRound } from 'lucide-react';
import {
  lazy,
  Suspense,
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
  setStudioWorkspaceTabPinned,
  type StudioWorkspaceTab,
} from './workspaceTabs';

const AssistantDrawer = lazy(async () => {
  const module = await import('@/features/ai-assistant');
  return { default: module.AssistantDrawer };
});

const STORAGE_KEY = 'quantx-studio-workspace-tabs';
const DEFAULT_WORKSPACE_PATH = '/';
const MAX_PERSISTED_TABS = 12;
const studioWorkspaceModes: StudioMode[] = [];
const studioWorkspaceTheme: StudioTheme = {
  icon: TrendingUp,
  name: 'red',
  title: 'QuantX Studio',
};

function StudioWorkspaceStatusBar({
  currentUserLabel,
}: {
  currentUserLabel: string;
}) {
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
          <span
            className="inline-flex min-w-0 items-center gap-1.5 normal-case tracking-normal text-slate-400"
            data-testid="studio-current-user"
            title={`当前用户：${currentUserLabel}`}
          >
            <UserRound className="h-3 w-3 shrink-0" />
            <span className="max-w-40 truncate">{currentUserLabel}</span>
          </span>
          <span className="text-slate-700">|</span>
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
    sizing: sidebar?.sizing ?? STUDIO_WORKSPACE_SIDEBAR_SIZING,
    storageFallback: sidebar?.sizing
      ? sidebar.title
      : STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE,
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
      style={{
        width: sidebarWidth,
      }}
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
      <div
        className="min-h-0 min-w-0 flex-1 overflow-hidden"
        data-testid="studio-sidebar-content"
      >
        {sidebar.content}
      </div>
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
      .map(item => {
        if (typeof item.path !== 'string') return null;
        const tab = buildStudioWorkspaceTab(item.path);
        return typeof item.isPreview === 'boolean'
          ? { ...tab, isPreview: item.isPreview }
          : tab;
      })
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
      tab.isDirty === other.isDirty &&
      tab.isPreview === other.isPreview &&
      tab.isPreviewable === other.isPreviewable &&
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

function writeWorkspaceTabs(
  tabs: StudioWorkspaceTab[],
  activeTabId: string | null
) {
  if (typeof window === 'undefined') return;

  try {
    const normalizedTabs = normalizeStudioWorkspaceTabs(tabs);
    let persistedTabs = normalizedTabs.slice(-MAX_PERSISTED_TABS);
    const activeTab = normalizedTabs.find(tab => tab.id === activeTabId);

    if (
      activeTab &&
      !persistedTabs.some(tab => tab.id === activeTab.id) &&
      persistedTabs.length > 0
    ) {
      const persistedIds = new Set(
        persistedTabs
          .slice(1)
          .map(tab => tab.id)
          .concat(activeTab.id)
      );
      persistedTabs = normalizedTabs.filter(tab => persistedIds.has(tab.id));
    }

    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        persistedTabs.map(({ id, isPreview, path }) => ({
          id,
          isPreview,
          path,
        }))
      )
    );
  } catch {
    // localStorage may be unavailable in hardened browser contexts.
  }
}

function getFallbackTab() {
  return buildStudioWorkspaceTab(DEFAULT_WORKSPACE_PATH);
}

export function StudioWorkspace({
  children,
  renderStatusBar,
}: {
  children: ReactNode;
  renderStatusBar?: (currentUserLabel: string) => ReactNode;
}) {
  const [location, setLocation] = useLocation();
  const { currentUserLabel, globalActions, utilityActions } =
    useStudioGlobalActions();
  const currentPath = getStudioWorkspacePath(location);
  const initialTab = buildStudioWorkspaceTab(currentPath);
  const [tabs, setTabs] = useState<StudioWorkspaceTab[]>(() => {
    const restoredTabs = readWorkspaceTabs();
    return mergeStudioWorkspaceTab(restoredTabs, initialTab);
  });
  const [activeTabId, setActiveTabId] = useState<string | null>(
    initialTab?.id || null
  );
  const [workspaceSidebar, setWorkspaceSidebarState] =
    useState<StudioWorkspaceSidebarConfig | null>(null);
  const [isAssistantOpen, setIsAssistantOpen] = useState(false);

  useEffect(() => {
    const currentTab = buildStudioWorkspaceTab(currentPath);

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
    writeWorkspaceTabs(tabs, activeTabId);
  }, [activeTabId, tabs]);

  const openStudioTab = useCallback(
    (path: string) => {
      const nextTab = buildStudioWorkspaceTab(path);

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

  const handleTabPin = useCallback((tabId: string, pinned: boolean) => {
    setTabs(currentTabs =>
      setStudioWorkspaceTabPinned(currentTabs, tabId, pinned)
    );
  }, []);

  const workspaceTabBar = useMemo(() => {
    const displayTabs = normalizeStudioWorkspaceTabTitles(tabs);

    return (
      <TabBar
        activeTabId={activeTabId}
        onTabChange={handleTabChange}
        onTabClose={handleTabClose}
        onTabPin={handleTabPin}
        renderTabContent={(tab: StudioWorkspaceTab, isActive) => {
          const Icon = tab.icon || BarChart3;
          return (
            <>
              <Icon
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  isActive ? 'text-red-400' : 'text-slate-400'
                )}
              />
              <span
                className={cn(
                  'min-w-0 flex-1 truncate text-xs font-bold',
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
  }, [activeTabId, handleTabChange, handleTabClose, handleTabPin, tabs]);

  const workspaceUtilityActions = useMemo(
    () => [
      {
        active: isAssistantOpen,
        icon: Bot,
        id: 'utility:ai-assistant',
        label: 'AI Assistant',
        onSelect: () => setIsAssistantOpen(value => !value),
      },
      ...utilityActions,
    ],
    [isAssistantOpen, utilityActions]
  );

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
        className="studio-workbench flex h-screen h-dvh min-h-0 w-full flex-col overflow-hidden bg-[var(--studio-bg)] text-slate-200 font-sans"
      >
        <div className="flex min-h-0 flex-1">
          <ActivityBar
            activeMode="WORKSPACE"
            globalActions={globalActions}
            modes={studioWorkspaceModes}
            onModeChange={() => undefined}
            theme={studioWorkspaceTheme}
            utilityActions={workspaceUtilityActions}
          />

          <div
            className="flex min-w-0 flex-1 flex-col bg-[#0b1120]/20"
            data-testid="studio-workspace-main"
          >
            <div className="flex h-10 shrink-0">
              <div className="min-w-0 flex-1">{workspaceTabBar}</div>
            </div>

            <div
              className="relative flex min-h-0 flex-1"
              data-testid="studio-workspace-content"
            >
              <StudioWorkspaceSidebarDock sidebar={workspaceSidebar} />
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                {children}
              </div>
              {isAssistantOpen && (
                <Suspense
                  fallback={
                    <aside className="h-full w-[420px] min-w-[360px] max-w-[46vw] shrink-0 border-l border-white/10 bg-[#080e1b]" />
                  }
                >
                  <AssistantDrawer
                    currentPath={currentPath}
                    onClose={() => setIsAssistantOpen(false)}
                  />
                </Suspense>
              )}
            </div>
          </div>
        </div>
        {renderStatusBar ? (
          renderStatusBar(currentUserLabel)
        ) : (
          <StudioWorkspaceStatusBar currentUserLabel={currentUserLabel} />
        )}
      </div>
    </StudioWorkspaceContext.Provider>
  );
}

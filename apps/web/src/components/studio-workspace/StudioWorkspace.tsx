import {
  BarChart3,
  Bell,
  BookOpen,
  Bot,
  Database,
  Grid2X2,
  History,
  Minus,
  Settings,
  Square,
  TrendingUp,
  UserRound,
  Wrench,
  X,
} from 'lucide-react';
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type React from 'react';
import { useLocation } from 'wouter';

import {
  ActivityBar,
  StatusBar,
  TabBar,
  type StudioAction,
  type StudioMode,
  type StudioTheme,
  useStudioGlobalActions,
} from '@/components/studio-workbench';
import {
  STUDIO_WORKSPACE_SIDEBAR_SIZING,
  STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE,
  useStudioSidebarSizing,
} from '@/components/studio-workbench/sidebarSizing';
import {
  STUDIO_CHROME_BACKGROUND,
  STUDIO_HEADER_HEIGHT,
  STUDIO_WORKSPACE_ACTIVE_TAB_STYLE,
  STUDIO_WORKSPACE_SURFACE_BACKGROUND,
  STUDIO_WORKSPACE_SURFACE_TOP,
  STUDIO_WORKSPACE_TAB_STYLE,
  STUDIO_WORKSPACE_WEAK_BORDER,
} from '@/components/studio-workbench/studioShellStyles';
import { getStudioThemeStyles } from '@/components/studio-workbench/themeStyles';
import { cn } from '@/utils/cn';

import {
  StudioWorkspaceContext,
  type StudioWorkspaceSidebarConfig,
} from './context';
import { QuantXLogo } from './QuantXLogo';
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
const ASSISTANT_PANEL_ID = 'studio-ai-assistant-panel';
const ASSISTANT_PANEL_STORAGE_SCOPE = 'studio-ai-assistant-panel';
const ASSISTANT_PANEL_SIZING = {
  defaultWidth: 400,
  maxWidth: 560,
  minWidth: 360,
  storageScope: ASSISTANT_PANEL_STORAGE_SCOPE,
};
const studioWorkspaceModes: StudioMode[] = [];
const studioWorkspaceTheme: StudioTheme = {
  icon: TrendingUp,
  name: 'blue',
  title: 'QuantX Studio',
};

function StudioChromeAction({
  badge = false,
  icon: Icon,
  label,
  onSelect,
}: {
  badge?: boolean;
  icon: React.ElementType;
  label: string;
  onSelect?: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className="group relative flex h-9 w-9 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={!onSelect}
      onClick={onSelect}
      title={label}
    >
      <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
      {badge && (
        <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-rose-400 ring-2 ring-[#0b1120]" />
      )}
    </button>
  );
}

function StudioWorkspaceHeader({
  currentUserLabel,
  isHomeActive,
  launcherActions,
  launcherTriggerRef,
  onAccount,
  onHome,
  onNotifications,
  onSettings,
  tabBar,
}: {
  currentUserLabel: string;
  isHomeActive: boolean;
  launcherActions: StudioAction[];
  launcherTriggerRef: React.RefObject<HTMLButtonElement>;
  onAccount?: () => void;
  onHome?: () => void;
  onNotifications?: () => void;
  onSettings?: () => void;
  tabBar: ReactNode;
}) {
  const [isLauncherOpen, setIsLauncherOpen] = useState(false);
  const launcherRef = useRef<HTMLDivElement>(null);
  const userMonogram = currentUserLabel.includes('管理')
    ? 'QA'
    : currentUserLabel
        .replace(/[^a-zA-Z]/g, '')
        .slice(0, 2)
        .toUpperCase() || 'QX';

  useEffect(() => {
    if (!isLauncherOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!launcherRef.current?.contains(event.target as Node)) {
        setIsLauncherOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsLauncherOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isLauncherOpen]);

  return (
    <header
      aria-label="QuantX Studio 工作区栏"
      className="studio-shell-header flex shrink-0 items-stretch"
      data-testid="studio-chrome-header"
      style={{
        background: STUDIO_CHROME_BACKGROUND,
        height: STUDIO_HEADER_HEIGHT,
      }}
    >
      <button
        type="button"
        onClick={onHome}
        className="studio-shell-brand flex shrink-0 items-center gap-2.5 text-left transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
        aria-label="QuantX Studio · 打开行情工作台"
        style={{
          paddingInline: 'clamp(10px, calc(100vw - 758px), 14px)',
          width: 'clamp(52px, calc(100vw - 716px), 172px)',
        }}
      >
        <QuantXLogo />
        <span className="hidden truncate text-[14px] font-semibold tracking-wide text-slate-100 md:block">
          QuantX Studio
        </span>
      </button>

      <nav
        aria-label="固定工作区"
        className="studio-shell-fixed-nav flex shrink-0 items-end"
      >
        <button
          type="button"
          onClick={onHome}
          className={cn(
            'relative flex w-12 items-center justify-center gap-2 border border-b-0 px-2 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 lg:px-3',
            isHomeActive
              ? 'text-slate-100'
              : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/5 hover:text-slate-100'
          )}
          style={{
            ...STUDIO_WORKSPACE_TAB_STYLE,
            width: 'clamp(48px, calc(100vw - 916px), 108px)',
            ...(isHomeActive ? STUDIO_WORKSPACE_ACTIVE_TAB_STYLE : {}),
          }}
          data-testid="studio-fixed-home-tab"
        >
          <BookOpen className="h-[18px] w-[18px]" strokeWidth={2} />
          <span className="hidden lg:inline">工作台</span>
          {isHomeActive && (
            <span
              aria-hidden="true"
              data-testid="studio-fixed-home-tab-connector"
              style={{
                background: STUDIO_WORKSPACE_SURFACE_TOP,
                bottom: -1,
                height: 1,
                left: 0,
                pointerEvents: 'none',
                position: 'absolute',
                right: 0,
              }}
            />
          )}
        </button>
      </nav>

      <div className="min-w-0 flex-1">{tabBar}</div>

      <div
        aria-label="工作区快捷操作"
        className="studio-shell-toolbar flex shrink-0 items-center gap-0.5 border-l border-white/10 px-1.5 md:gap-1 md:px-2.5"
        role="toolbar"
        style={{
          borderColor: 'rgba(111, 151, 194, 0.2)',
        }}
      >
        <div className="relative" ref={launcherRef}>
          <button
            ref={launcherTriggerRef}
            type="button"
            aria-expanded={isLauncherOpen}
            aria-haspopup="menu"
            aria-label="打开功能启动器"
            className={cn(
              'flex h-9 w-9 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
              isLauncherOpen && 'bg-white/5 text-slate-100'
            )}
            onClick={() => setIsLauncherOpen(value => !value)}
          >
            <Grid2X2 className="h-[18px] w-[18px]" strokeWidth={2} />
          </button>
          {isLauncherOpen && (
            <div
              className="absolute right-0 grid grid-cols-3 gap-1 rounded-lg border border-white/10 p-2 shadow-2xl shadow-black/50"
              role="menu"
              style={{
                background: '#0b1627',
                top: 40,
                width: 360,
                zIndex: 80,
              }}
            >
              {launcherActions.map(action => {
                const Icon = action.icon;
                return (
                  <button
                    key={action.id}
                    type="button"
                    className="flex min-w-0 items-center gap-2 rounded-md px-2.5 py-2 text-left text-[11px] text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                    onClick={() => {
                      setIsLauncherOpen(false);
                      action.onSelect();
                    }}
                    role="menuitem"
                    title={action.label}
                  >
                    <Icon className="h-4 w-4 shrink-0 text-slate-500" />
                    <span className="truncate">
                      {action.shortLabel || action.label}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <StudioChromeAction
          badge
          icon={Bell}
          label="查看通知"
          onSelect={onNotifications}
        />
        <StudioChromeAction
          icon={Settings}
          label="打开系统设置"
          onSelect={onSettings}
        />
        <span aria-hidden="true" className="mx-1 h-6 w-px bg-white/10" />
        <button
          type="button"
          onClick={onAccount}
          className="flex h-8 min-w-9 items-center justify-center rounded-full border border-white/10 bg-white/5 px-2 font-mono text-[10px] font-semibold text-slate-200 transition-colors hover:border-white/20 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          aria-label={`打开账户：${currentUserLabel}`}
          title={`当前用户：${currentUserLabel}`}
          style={{
            background: '#0d1a2b',
            borderColor: '#263b53',
            boxShadow: 'inset 0 1px 0 rgba(148, 190, 230, 0.07)',
          }}
        >
          {userMonogram}
        </button>
        <div
          aria-hidden="true"
          className="ml-1 hidden h-full items-center border-l border-white/10 pl-1 xl:flex"
          style={{ borderColor: 'rgba(111, 151, 194, 0.2)' }}
        >
          <span className="flex h-8 w-8 items-center justify-center text-slate-400">
            <Minus className="h-3.5 w-3.5" />
          </span>
          <span className="flex h-8 w-8 items-center justify-center text-slate-400">
            <Square className="h-3 w-3" />
          </span>
          <span className="flex h-8 w-8 items-center justify-center text-slate-400">
            <X className="h-3.5 w-3.5" />
          </span>
        </div>
      </div>
    </header>
  );
}

function StudioWorkspaceStatusBar({
  currentUserLabel,
}: {
  currentUserLabel: string;
}) {
  return (
    <StatusBar
      variant="workspace"
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
  const themeStyles = getStudioThemeStyles(sidebar?.themeName ?? 'blue');

  if (!sidebar?.showSidebar || !sidebar.content) return null;

  return (
    <aside
      data-testid="studio-sidebar-dock"
      className={cn(
        'relative flex h-full min-h-0 shrink-0 flex-col border-r border-white/5 bg-[#0b1120]',
        !isResizingSidebar && 'transition-[width] duration-150'
      )}
      style={{
        width: sidebarWidth,
        borderColor: 'rgba(111, 151, 194, 0.14)',
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

function StudioAssistantDock({
  currentPath,
  isOpen,
  onClose,
  onKeyDown,
}: {
  currentPath: string;
  isOpen: boolean;
  onClose: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
}) {
  const {
    handleSidebarResizeKeyDown,
    handleSidebarResizeStart,
    isResizingSidebar,
    maxSidebarWidth,
    minSidebarWidth,
    sidebarWidth,
  } = useStudioSidebarSizing({
    resizeEdge: 'left',
    sizing: ASSISTANT_PANEL_SIZING,
    storageFallback: ASSISTANT_PANEL_STORAGE_SCOPE,
  });

  if (!isOpen) return null;

  return (
    <div
      id={ASSISTANT_PANEL_ID}
      className={cn(
        'absolute inset-y-0 right-0 z-40 flex h-full min-w-0 max-w-full shrink-0 flex-col shadow-2xl shadow-black/40 2xl:relative 2xl:inset-y-auto 2xl:right-auto 2xl:z-auto 2xl:max-w-none',
        !isResizingSidebar &&
          'transition-[width] duration-150 motion-reduce:transition-none'
      )}
      data-testid="studio-assistant-panel"
      onKeyDown={onKeyDown}
      style={{ width: sidebarWidth }}
    >
      <div
        role="separator"
        aria-label="AI 助手面板宽度"
        aria-orientation="vertical"
        aria-valuemin={minSidebarWidth}
        aria-valuemax={maxSidebarWidth}
        aria-valuenow={sidebarWidth}
        className={cn(
          'group absolute -left-1.5 top-0 z-50 h-full w-3 cursor-col-resize touch-none outline-none',
          isResizingSidebar && 'bg-cyan-500/5'
        )}
        data-testid="studio-assistant-resizer"
        tabIndex={0}
        onKeyDown={handleSidebarResizeKeyDown}
        onPointerDown={handleSidebarResizeStart}
      >
        <div
          className={cn(
            'absolute left-1/2 top-0 h-full w-px -translate-x-1/2 transition-colors duration-150 motion-reduce:transition-none',
            isResizingSidebar
              ? 'bg-cyan-400'
              : 'bg-white/5 group-hover:bg-cyan-400/70 group-focus-visible:bg-cyan-400/70'
          )}
        />
        <div
          className={cn(
            'absolute left-1/2 top-1/2 h-12 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full transition-opacity duration-150 motion-reduce:transition-none',
            isResizingSidebar
              ? 'bg-cyan-400 opacity-100'
              : 'bg-slate-500/60 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'
          )}
        />
      </div>

      <Suspense
        fallback={
          <aside className="h-full w-full min-w-0 border-l border-white/10 bg-[#080e1b]" />
        }
      >
        <AssistantDrawer currentPath={currentPath} onClose={onClose} />
      </Suspense>
    </div>
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
  activityStatus,
  children,
  renderStatusBar,
}: {
  activityStatus?: {
    detail: string;
    label: string;
    tone: 'blocked' | 'checking' | 'ready' | 'reduce-only';
  };
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
  const assistantTriggerRef = useRef<HTMLButtonElement>(null);

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

  const closeAssistant = useCallback(() => {
    setIsAssistantOpen(false);
    assistantTriggerRef.current?.focus();
  }, []);

  const toggleAssistant = useCallback(() => {
    if (isAssistantOpen) {
      closeAssistant();
      return;
    }
    setIsAssistantOpen(true);
  }, [closeAssistant, isAssistantOpen]);

  const handleAssistantKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key !== 'Escape' || !isAssistantOpen) return;
      event.preventDefault();
      event.stopPropagation();
      closeAssistant();
    },
    [closeAssistant, isAssistantOpen]
  );

  const workspaceTabBar = useMemo(() => {
    const displayTabs = normalizeStudioWorkspaceTabTitles(tabs).filter(
      tab => tab.path !== '/'
    );

    return (
      <TabBar
        activeTabId={
          displayTabs.some(tab => tab.id === activeTabId) ? activeTabId : null
        }
        createTooltip="打开行情工作台"
        onTabChange={handleTabChange}
        onTabClose={handleTabClose}
        onTabCreate={() => openStudioTab(DEFAULT_WORKSPACE_PATH)}
        onTabPin={handleTabPin}
        renderTabContent={(tab: StudioWorkspaceTab, isActive) => {
          const Icon = tab.icon || BarChart3;
          return (
            <>
              <Icon
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  isActive ? 'text-blue-400' : 'text-slate-400'
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
        themeColor="blue"
        variant="workspace"
      />
    );
  }, [
    activeTabId,
    handleTabChange,
    handleTabClose,
    handleTabPin,
    openStudioTab,
    tabs,
  ]);

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
  const accountAction = utilityActions.find(
    action => action.id === 'utility:assets'
  );
  const notificationAction = utilityActions.find(
    action => action.id === 'utility:notifications'
  );
  const settingsAction = utilityActions.find(
    action => action.id === 'nav:/settings'
  );
  const findNavigationAction = (path: string) =>
    globalActions.find(action => action.id === `nav:${path}`);
  const researchAction = findNavigationAction('/research');
  const strategiesAction = findNavigationAction('/strategies');
  const holdingsAction = findNavigationAction('/holdings');
  const toolsAction = findNavigationAction('/t-trade');
  const railActions: StudioAction[] = [];
  if (researchAction) {
    railActions.push({
      ...researchAction,
      id: 'rail:research',
      shortLabel: '研究',
    });
  }
  if (strategiesAction) {
    railActions.push({
      ...strategiesAction,
      id: 'rail:strategies',
      shortLabel: '策略',
    });
  }
  railActions.push({
    active: false,
    icon: History,
    id: 'rail:backtest',
    label: '回测与研究运行',
    onSelect: () => openStudioTab('/research'),
    shortLabel: '回测',
  });
  if (holdingsAction) {
    railActions.push({
      ...holdingsAction,
      id: 'rail:trading',
      shortLabel: '交易',
    });
  }
  if (accountAction) {
    railActions.push({
      ...accountAction,
      active: currentPath.startsWith('/account'),
      id: 'rail:portfolio',
      shortLabel: '组合',
    });
  }
  railActions.push({
    active: currentPath.startsWith('/settings/data'),
    icon: Database,
    id: 'rail:data',
    label: '数据管理',
    onSelect: () => openStudioTab('/settings/data'),
    shortLabel: '数据',
  });
  if (toolsAction) {
    railActions.push({
      ...toolsAction,
      icon: Wrench,
      id: 'rail:tools',
      shortLabel: '工具',
    });
  }
  const railUtilityActions = utilityActions.filter(action =>
    ['utility:notifications', 'nav:/settings'].includes(action.id)
  );
  const launcherActions = [
    {
      icon: Bot,
      id: 'workspace:assistant',
      label: 'AI 助手',
      onSelect: toggleAssistant,
      shortLabel: 'AI 助手',
    },
    ...globalActions,
    ...utilityActions.filter(action =>
      [
        'utility:assets',
        'utility:developer-docs',
        'utility:logout',
        'nav:/settings',
      ].includes(action.id)
    ),
  ];

  return (
    <StudioWorkspaceContext.Provider value={contextValue}>
      <div
        data-studio-workbench
        className="studio-shell studio-workbench flex h-screen h-dvh min-h-0 w-full flex-col overflow-hidden text-slate-200 font-sans"
        style={{
          background:
            'radial-gradient(circle at 66% -18%, rgba(24, 67, 108, 0.12), transparent 38%), #050b16',
        }}
      >
        <StudioWorkspaceHeader
          currentUserLabel={currentUserLabel}
          isHomeActive={currentPath === '/'}
          launcherActions={launcherActions}
          launcherTriggerRef={assistantTriggerRef}
          onAccount={accountAction?.onSelect}
          onHome={() => openStudioTab('/')}
          onNotifications={notificationAction?.onSelect}
          onSettings={settingsAction?.onSelect}
          tabBar={workspaceTabBar}
        />

        <div className="studio-shell-body flex min-h-0 flex-1">
          <ActivityBar
            activeMode="WORKSPACE"
            environmentStatus={activityStatus}
            globalActions={railActions}
            modes={studioWorkspaceModes}
            onModeChange={() => undefined}
            theme={studioWorkspaceTheme}
            utilityActions={railUtilityActions}
            variant="studio"
          />

          <div
            className="studio-shell-main flex min-w-0 flex-1 flex-col overflow-hidden"
            data-testid="studio-workspace-main"
            style={{
              background: STUDIO_WORKSPACE_SURFACE_BACKGROUND,
              borderTopLeftRadius: 12,
              boxShadow: `inset 0 1px 0 ${STUDIO_WORKSPACE_WEAK_BORDER}`,
            }}
          >
            <div
              className="relative flex min-h-0 min-w-0 flex-1"
              data-testid="studio-workspace-content"
            >
              <StudioWorkspaceSidebarDock sidebar={workspaceSidebar} />
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                {children}
              </div>
              <StudioAssistantDock
                currentPath={currentPath}
                isOpen={isAssistantOpen}
                onClose={closeAssistant}
                onKeyDown={handleAssistantKeyDown}
              />
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

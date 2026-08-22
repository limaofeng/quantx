import {
  Activity,
  BarChart3,
  Briefcase,
  ClipboardList,
  Copy,
  Hand,
  History,
  RefreshCw,
  Search,
  ShieldCheck,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useLocation } from 'wouter';

import {
  StudioMenu,
  StudioWorkbench,
  TabBar,
  type StudioMode,
  type StudioTab,
  useStudioMenu,
} from '@/components/studio-workbench';
import { cn } from '@/utils/cn';

export type PortfolioStudioMode =
  'ACCOUNT' | 'AUDIT' | 'HISTORY' | 'HOLDINGS' | 'LIQUIDATION';

interface PortfolioResourceItem {
  description: string;
  icon: LucideIcon;
  id: PortfolioStudioMode;
  label: string;
  path: string;
}

interface PortfolioStudioTab extends StudioTab {
  payload: PortfolioResourceItem;
}

const portfolioResources: PortfolioResourceItem[] = [
  {
    description: '持仓列表、收益、仓位占比',
    icon: Briefcase,
    id: 'HOLDINGS',
    label: '持仓总览',
    path: '/holdings',
  },
  {
    description: '批量选择、清仓执行、赎回入口',
    icon: Hand,
    id: 'LIQUIDATION',
    label: '清仓执行',
    path: '/liquidation',
  },
  {
    description: '已清仓标的、实现盈亏',
    icon: History,
    id: 'HISTORY',
    label: '清仓历史',
    path: '/liquidation',
  },
  {
    description: '账户资产、现金、持仓市值',
    icon: Wallet,
    id: 'ACCOUNT',
    label: '账户资产',
    path: '/holdings',
  },
  {
    description: '危险动作、确认记录、审计线索',
    icon: ShieldCheck,
    id: 'AUDIT',
    label: '操作审计',
    path: '/liquidation',
  },
];

const portfolioModes: StudioMode[] = portfolioResources.map(resource => ({
  icon: resource.icon,
  id: resource.id,
  label: resource.label,
}));

function getPortfolioResource(mode: PortfolioStudioMode) {
  return (
    portfolioResources.find(resource => resource.id === mode) ||
    portfolioResources[0]
  );
}

function copyText(text: string) {
  if (!navigator.clipboard) return;
  void navigator.clipboard.writeText(text);
}

interface PortfolioStudioShellProps {
  activeMode: PortfolioStudioMode;
  className?: string;
  content: ReactNode;
  extraSidebar?: ReactNode;
  onModeChange?: (mode: PortfolioStudioMode) => void;
  showSidebar?: boolean;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
  tabBar?: ReactNode;
}

export function PortfolioStudioShell({
  activeMode,
  className,
  content,
  extraSidebar,
  onModeChange,
  showSidebar = true,
  statusBarLeft,
  statusBarRight,
  tabBar,
}: PortfolioStudioShellProps) {
  const [, setLocation] = useLocation();
  const [resourceSearch, setResourceSearch] = useState('');
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<PortfolioResourceItem>();
  const activeResource = getPortfolioResource(activeMode);
  const filteredResources = useMemo(() => {
    const keyword = resourceSearch.trim().toLowerCase();
    if (!keyword) return portfolioResources;

    return portfolioResources.filter(resource => {
      return (
        resource.label.toLowerCase().includes(keyword) ||
        resource.description.toLowerCase().includes(keyword) ||
        resource.path.toLowerCase().includes(keyword)
      );
    });
  }, [resourceSearch]);
  const tabs = useMemo<PortfolioStudioTab[]>(
    () =>
      portfolioResources.map(resource => ({
        icon: resource.icon,
        id: resource.id,
        name: resource.label,
        payload: resource,
        type: 'portfolio-resource',
      })),
    []
  );

  const changeMode = (mode: PortfolioStudioMode) => {
    if (onModeChange) {
      onModeChange(mode);
      return;
    }

    setLocation(getPortfolioResource(mode).path);
  };

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className={className}
      content={content}
      isPage
      modes={portfolioModes}
      onModeChange={mode => changeMode(mode as PortfolioStudioMode)}
      showSidebar={showSidebar}
      sidebar={
        <aside className="flex h-full min-h-0 flex-col">
          <div className="border-b border-white/5 px-4 py-3">
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.24em] text-blue-400">
              <Activity className="h-3.5 w-3.5" />
              Portfolio Studio
            </div>
            <div className="mt-1 text-xs font-medium leading-relaxed text-slate-500">
              持仓、清仓、历史与资产状态统一工作台。
            </div>
            <label className="mt-3 flex h-8 items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 text-slate-500 focus-within:border-blue-500/40">
              <Search className="h-3.5 w-3.5 shrink-0" />
              <input
                value={resourceSearch}
                onChange={event => setResourceSearch(event.target.value)}
                placeholder="搜索投资组合资源"
                className="min-w-0 flex-1 bg-transparent text-xs font-medium text-slate-200 outline-none placeholder:text-slate-600"
              />
            </label>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
            <div className="mb-2 px-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
              Resources
            </div>
            <div className="space-y-1">
              {filteredResources.map(resource => {
                const Icon = resource.icon;
                const isActive = resource.id === activeMode;

                return (
                  <button
                    key={resource.id}
                    type="button"
                    onClick={() => changeMode(resource.id)}
                    onContextMenu={event => openAtPointer(event, resource)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                      isActive
                        ? 'border-blue-500/30 bg-blue-500/10 text-blue-100'
                        : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-bold">
                        {resource.label}
                      </span>
                      <span className="block truncate text-[10px] font-medium text-slate-600">
                        {resource.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            {extraSidebar && (
              <div className="mt-4 border-t border-white/5 pt-3">
                {extraSidebar}
              </div>
            )}
          </div>

          <div className="border-t border-white/5 p-3">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="flex h-8 w-full items-center justify-center gap-2 rounded-md border border-white/10 text-[10px] font-black uppercase tracking-wider text-slate-400 transition-colors hover:border-blue-500/40 hover:text-blue-300"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              刷新组合数据
            </button>
          </div>

          <StudioMenu
            ariaLabel="投资组合资源菜单"
            items={[
              {
                icon: <BarChart3 className="h-3.5 w-3.5" />,
                id: 'open',
                label: '打开资源',
                onSelect: () => {
                  if (menu?.payload) changeMode(menu.payload.id);
                },
              },
              {
                icon: <Copy className="h-3.5 w-3.5" />,
                id: 'copy-path',
                label: '复制路径',
                onSelect: () => {
                  if (menu?.payload) copyText(menu.payload.path);
                },
              },
              { id: 'separator-refresh', type: 'separator' },
              {
                icon: <RefreshCw className="h-3.5 w-3.5" />,
                id: 'refresh',
                label: '刷新页面',
                onSelect: () => window.location.reload(),
              },
            ]}
            menu={menu}
            onClose={closeMenu}
            width={180}
          />
        </aside>
      }
      sidebarSizing={{
        defaultWidth: 304,
        maxWidth: 430,
        minWidth: 250,
        storageScope: 'portfolio-studio',
      }}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              组合工作台
            </span>
            <span className="text-slate-700">|</span>
            <span>{activeResource.label}</span>
          </>
        )
      }
      statusBarRight={
        statusBarRight || (
          <>
            <span>{activeResource.description}</span>
            <span className="text-slate-700">|</span>
            <span className="font-mono">{activeResource.path}</span>
          </>
        )
      }
      tabBar={
        tabBar || (
          <TabBar
            activeTabId={activeMode}
            closable={false}
            onTabChange={tabId => changeMode(tabId as PortfolioStudioMode)}
            onTabClose={() => undefined}
            renderTabContent={(tab: PortfolioStudioTab, isActive) => {
              const Icon = tab.icon || ClipboardList;
              return (
                <>
                  <Icon
                    className={cn(
                      'h-3.5 w-3.5 shrink-0',
                      isActive ? 'text-blue-400' : 'text-slate-500'
                    )}
                  />
                  <span className="max-w-[120px] truncate text-[11px] font-black">
                    {tab.name}
                  </span>
                </>
              );
            }}
            tabs={tabs}
            themeColor="blue"
          />
        )
      }
      theme={{
        icon: Briefcase,
        name: 'blue',
        title: 'QuantX Portfolio Studio',
      }}
    />
  );
}

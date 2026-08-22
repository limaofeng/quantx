import {
  BarChart3,
  Briefcase,
  CalendarDays,
  CandlestickChart,
  ClipboardList,
  Copy,
  Database,
  Filter,
  Landmark,
  LineChart,
  Megaphone,
  RefreshCw,
  Search,
  WalletCards,
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

export type DataStudioMode =
  | 'ANNOUNCEMENTS'
  | 'CALENDAR'
  | 'FINANCIAL'
  | 'FLOWS'
  | 'HOLDINGS'
  | 'MARKET'
  | 'MARKET_DATA'
  | 'OVERVIEW'
  | 'REVERSE_REPO'
  | 'SCREENING'
  | 'SECTORS'
  | 'STOCKS';

interface DataResourceItem {
  description: string;
  icon: LucideIcon;
  id: DataStudioMode;
  label: string;
  path: string;
}

interface DataStudioTab extends StudioTab {
  payload: DataResourceItem;
}

const dataResources: DataResourceItem[] = [
  {
    description: '门户、全局同步、系统健康',
    icon: Database,
    id: 'OVERVIEW',
    label: '数据门户',
    path: '/settings/data',
  },
  {
    description: '行情、指数、市场概览',
    icon: BarChart3,
    id: 'MARKET',
    label: '市场数据',
    path: '/settings/data/market',
  },
  {
    description: '单票覆盖、缺口、手动补拉',
    icon: Search,
    id: 'STOCKS',
    label: '个股数据',
    path: '/settings/data/stocks',
  },
  {
    description: 'K线、tick、批量行情缓存',
    icon: CandlestickChart,
    id: 'MARKET_DATA',
    label: 'K线同步',
    path: '/settings/data/market-data',
  },
  {
    description: '行业、概念、板块持仓',
    icon: LineChart,
    id: 'SECTORS',
    label: '板块数据',
    path: '/settings/data/sectors',
  },
  {
    description: '交易日、休市、开盘状态',
    icon: CalendarDays,
    id: 'CALENDAR',
    label: '交易日历',
    path: '/settings/data/calendar',
  },
  {
    description: '本地持仓数据同步',
    icon: Briefcase,
    id: 'HOLDINGS',
    label: '持仓同步',
    path: '/settings/data/holdings',
  },
  {
    description: '交易流水、委托成交数据',
    icon: ClipboardList,
    id: 'FLOWS',
    label: '交易流水',
    path: '/settings/data/transactions',
  },
  {
    description: '财报、指标、财务快照',
    icon: Landmark,
    id: 'FINANCIAL',
    label: '财务数据',
    path: '/settings/data/financial',
  },
  {
    description: '公告、回购、披露事件',
    icon: Megaphone,
    id: 'ANNOUNCEMENTS',
    label: '公告同步',
    path: '/settings/data/announcements',
  },
  {
    description: '国债逆回购、利率与交易记录',
    icon: WalletCards,
    id: 'REVERSE_REPO',
    label: '逆回购',
    path: '/settings/data/reverse-repo',
  },
  {
    description: '条件构建、结果表格',
    icon: Filter,
    id: 'SCREENING',
    label: '股票筛选',
    path: '/screening',
  },
];

const dataModes: StudioMode[] = dataResources.map(resource => ({
  id: resource.id,
  icon: resource.icon,
  label: resource.label,
}));

function getDataStudioResource(mode: DataStudioMode) {
  return (
    dataResources.find(resource => resource.id === mode) || dataResources[0]
  );
}

interface DataStudioShellProps {
  activeMode: DataStudioMode;
  className?: string;
  content: ReactNode;
  extraSidebar?: ReactNode;
  showSidebar?: boolean;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
  tabBar?: ReactNode;
  tabBarTrailing?: ReactNode;
}

function copyText(text: string) {
  if (!navigator.clipboard) return;
  void navigator.clipboard.writeText(text);
}

export function DataStudioShell({
  activeMode,
  className,
  content,
  extraSidebar,
  showSidebar = true,
  statusBarLeft,
  statusBarRight,
  tabBar,
  tabBarTrailing,
}: DataStudioShellProps) {
  const [, setLocation] = useLocation();
  const [resourceSearch, setResourceSearch] = useState('');
  const { closeMenu, menu, openAtPointer } = useStudioMenu<DataResourceItem>();
  const activeResource = getDataStudioResource(activeMode);
  const tabs = useMemo<DataStudioTab[]>(
    () =>
      dataResources.map(resource => ({
        icon: resource.icon,
        id: resource.id,
        name: resource.label,
        payload: resource,
        type: 'data-resource',
      })),
    []
  );
  const filteredResources = useMemo(() => {
    const keyword = resourceSearch.trim().toLowerCase();
    if (!keyword) return dataResources;

    return dataResources.filter(resource => {
      return (
        resource.label.toLowerCase().includes(keyword) ||
        resource.description.toLowerCase().includes(keyword) ||
        resource.path.toLowerCase().includes(keyword)
      );
    });
  }, [resourceSearch]);

  const changeMode = (mode: DataStudioMode) => {
    const resource = getDataStudioResource(mode);
    setLocation(resource.path);
  };

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className={className}
      content={content}
      isPage
      modes={dataModes}
      onModeChange={mode => changeMode(mode as DataStudioMode)}
      showSidebar={showSidebar}
      sidebar={
        <aside className="flex h-full min-h-0 flex-col">
          <div className="border-b border-white/5 px-4 py-3">
            <div className="text-[10px] font-black uppercase tracking-[0.24em] text-blue-400">
              Data Studio
            </div>
            <div className="mt-1 text-xs font-medium leading-relaxed text-slate-500">
              市场数据、同步任务、筛选结果统一入口。
            </div>
            <label className="mt-3 flex h-8 items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 text-slate-500 focus-within:border-blue-500/40">
              <Search className="h-3.5 w-3.5 shrink-0" />
              <input
                value={resourceSearch}
                onChange={event => setResourceSearch(event.target.value)}
                placeholder="搜索资源"
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
              刷新当前资源
            </button>
          </div>

          <StudioMenu
            ariaLabel="数据资源菜单"
            items={[
              {
                icon: <Database className="h-3.5 w-3.5" />,
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
        maxWidth: 440,
        minWidth: 248,
        storageScope: 'data-studio',
      }}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              数据资源
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
          <div className="flex h-10 shrink-0 items-center border-b border-white/5 bg-[#0b1120]/80">
            <div className="min-w-0 flex-1">
              <TabBar
                activeTabId={activeMode}
                closable={false}
                onTabChange={tabId => changeMode(tabId as DataStudioMode)}
                onTabClose={() => undefined}
                renderTabContent={(tab: DataStudioTab, isActive) => {
                  const Icon = tab.icon || Database;
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
            </div>
            {tabBarTrailing && (
              <div className="hidden h-full shrink-0 items-center border-l border-white/5 px-3 xl:flex">
                {tabBarTrailing}
              </div>
            )}
          </div>
        )
      }
      theme={{
        icon: Database,
        name: 'blue',
        title: 'QuantX Data Studio',
      }}
    />
  );
}

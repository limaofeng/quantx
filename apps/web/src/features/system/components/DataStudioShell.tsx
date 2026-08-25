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
  StudioResourceSidebar,
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
        <>
          <StudioResourceSidebar
            activeId={activeMode}
            description="市场数据、同步任务、筛选结果统一入口。"
            eyebrow="Data Studio"
            footerActionLabel="刷新当前资源"
            items={filteredResources}
            listExtra={extraSidebar}
            onFooterAction={() => window.location.reload()}
            onItemContextMenu={openAtPointer}
            onItemSelect={changeMode}
            onSearchChange={setResourceSearch}
            searchPlaceholder="搜索资源"
            searchValue={resourceSearch}
          />
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
        </>
      }
      sidebarSizing={{
        defaultWidth: 280,
        maxWidth: 420,
        minWidth: 220,
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
          <div className="flex h-studio-tab shrink-0 items-center border-b border-white/5 bg-[#0b1120]/80">
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
                      <span className="max-w-[120px] truncate text-ui-caption font-black">
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

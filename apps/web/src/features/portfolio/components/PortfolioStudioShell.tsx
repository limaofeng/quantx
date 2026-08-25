import {
  BarChart3,
  Briefcase,
  ClipboardList,
  Copy,
  Hand,
  History,
  RefreshCw,
  ShieldCheck,
  Wallet,
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
        <>
          <StudioResourceSidebar
            activeId={activeMode}
            description="持仓、清仓、历史与资产状态统一工作台。"
            eyebrow="Portfolio Studio"
            footerActionLabel="刷新组合数据"
            items={filteredResources}
            listExtra={extraSidebar}
            onFooterAction={() => window.location.reload()}
            onItemContextMenu={openAtPointer}
            onItemSelect={changeMode}
            onSearchChange={setResourceSearch}
            searchPlaceholder="搜索投资组合资源"
            searchValue={resourceSearch}
          />
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
        </>
      }
      sidebarSizing={{
        defaultWidth: 280,
        maxWidth: 420,
        minWidth: 220,
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
                  <span className="max-w-[120px] truncate text-ui-caption font-black">
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

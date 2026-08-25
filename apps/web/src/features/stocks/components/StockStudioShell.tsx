import {
  ArrowLeft,
  BarChart3,
  Bot,
  CandlestickChart,
  Copy,
  DollarSign,
  FileText,
  LineChart,
  ReceiptText,
  RefreshCw,
  type LucideIcon,
} from 'lucide-react';
import { useMemo, type ReactNode } from 'react';
import { Link } from 'wouter';

import {
  StudioMenu,
  StudioResourceSidebar,
  StudioWorkbench,
  TabBar,
  type StudioMode,
  type StudioTab,
  useStudioMenu,
} from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';

export type StockStudioMode =
  'CHART' | 'FINANCIAL' | 'FLOWS' | 'QUOTE' | 'STRATEGIES';

interface StockResourceItem {
  description: string;
  icon: LucideIcon;
  id: StockStudioMode;
  label: string;
}

interface StockStudioTab extends StudioTab {
  payload: StockResourceItem;
}

const stockResources: StockResourceItem[] = [
  {
    description: '报价、涨跌幅、成交量',
    icon: CandlestickChart,
    id: 'QUOTE',
    label: '实时报价',
  },
  {
    description: 'K 线、盘口、技术视图',
    icon: LineChart,
    id: 'CHART',
    label: '图表',
  },
  {
    description: '财务快照、估值与基本面',
    icon: FileText,
    id: 'FINANCIAL',
    label: '财务',
  },
  {
    description: '委托、成交与资金流水',
    icon: ReceiptText,
    id: 'FLOWS',
    label: '流水',
  },
  {
    description: '关联策略、回测与运行实例',
    icon: Bot,
    id: 'STRATEGIES',
    label: '策略',
  },
];

const stockModes: StudioMode[] = stockResources.map(resource => ({
  icon: resource.icon,
  id: resource.id,
  label: resource.label,
}));

function getStockResource(mode: StockStudioMode) {
  return (
    stockResources.find(resource => resource.id === mode) || stockResources[0]
  );
}

function copyText(text: string) {
  if (!navigator.clipboard) return;
  void navigator.clipboard.writeText(text);
}

interface StockStudioShellProps {
  activeMode: StockStudioMode;
  content: ReactNode;
  onModeChange: (mode: StockStudioMode) => void;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
  stockCode: string;
  stockName?: string | null;
}

export function StockStudioShell({
  activeMode,
  content,
  onModeChange,
  statusBarLeft,
  statusBarRight,
  stockCode,
  stockName,
}: StockStudioShellProps) {
  const openStudioTab = useStudioNavigate();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<StockResourceItem>();
  const activeResource = getStockResource(activeMode);
  const tabs = useMemo<StockStudioTab[]>(
    () =>
      stockResources.map(resource => ({
        icon: resource.icon,
        id: resource.id,
        name: resource.label,
        payload: resource,
        type: 'stock-resource',
      })),
    []
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      content={content}
      isPage
      modes={stockModes}
      onModeChange={mode => onModeChange(mode as StockStudioMode)}
      sidebar={
        <>
          <StudioResourceSidebar
            activeId={activeMode}
            eyebrow="Stock Studio"
            footerActionLabel="刷新个股"
            headerExtra={
              <>
                <div className="mt-2 rounded-control border border-blue-500/20 bg-blue-500/10 p-ui-panel">
                  <div className="truncate text-ui-body font-bold text-blue-100">
                    {stockName || stockCode || '未选择标的'}
                  </div>
                  <div className="mt-1 font-mono text-ui-label font-semibold text-blue-400/80">
                    {stockCode || 'N/A'}
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Link href="/holdings">
                    <button
                      type="button"
                      className="flex h-control-compact w-full cursor-pointer items-center justify-center gap-2 rounded-control border border-white/10 text-ui-caption font-semibold text-slate-400 transition-colors hover:border-blue-500/40 hover:text-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                    >
                      <ArrowLeft className="h-3.5 w-3.5" />
                      持仓
                    </button>
                  </Link>
                  <button
                    type="button"
                    onClick={() =>
                      openStudioTab(`/holdings?symbol=${stockCode}`)
                    }
                    className="flex h-control-compact w-full cursor-pointer items-center justify-center gap-2 rounded-control border border-blue-500/30 bg-blue-500/10 text-ui-caption font-semibold text-blue-100 transition-colors hover:bg-blue-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  >
                    <DollarSign className="h-3.5 w-3.5" />
                    交易
                  </button>
                </div>
              </>
            }
            items={stockResources}
            onFooterAction={() => window.location.reload()}
            onItemContextMenu={openAtPointer}
            onItemSelect={onModeChange}
            sectionLabel="Views"
          />
          <StudioMenu
            ariaLabel="个股视图菜单"
            items={[
              {
                icon: <BarChart3 className="h-3.5 w-3.5" />,
                id: 'open',
                label: '切换视图',
                onSelect: () => {
                  if (menu?.payload) onModeChange(menu.payload.id);
                },
              },
              {
                icon: <Copy className="h-3.5 w-3.5" />,
                id: 'copy-code',
                label: '复制股票代码',
                onSelect: () => copyText(stockCode),
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
        storageScope: 'stock-studio',
      }}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              个股详情
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
            <span className="font-mono">{stockCode || 'N/A'}</span>
          </>
        )
      }
      tabBar={
        <TabBar
          activeTabId={activeMode}
          closable={false}
          onTabChange={tabId => onModeChange(tabId as StockStudioMode)}
          onTabClose={() => undefined}
          tabs={tabs}
          themeColor="blue"
        />
      }
      theme={{
        icon: CandlestickChart,
        name: 'blue',
        title: 'QuantX Stock Studio',
      }}
    />
  );
}

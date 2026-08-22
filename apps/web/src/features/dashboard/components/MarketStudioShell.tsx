import { BarChart3 } from 'lucide-react';
import { useMemo, type ReactNode } from 'react';

import {
  StudioWorkbench,
  TabBar,
  type StudioMode,
  type StudioTab,
} from '@/components/studio-workbench';

export type MarketStudioMode = 'MARKET';

const marketDescription = '大盘全景、热点机会、个股排行';

const marketModes: StudioMode[] = [
  {
    icon: BarChart3,
    id: 'MARKET',
    label: '行情工作台',
  },
];

interface MarketStudioShellProps {
  content: ReactNode;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
}

export function MarketStudioShell({
  content,
  statusBarLeft,
  statusBarRight,
}: MarketStudioShellProps) {
  const activeMode: MarketStudioMode = 'MARKET';
  const tabs = useMemo<StudioTab[]>(
    () =>
      marketModes.map(mode => ({
        icon: mode.icon,
        id: mode.id,
        name: mode.label,
        type: 'market-resource',
      })),
    []
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      content={content}
      isPage
      modes={marketModes}
      onModeChange={() => undefined}
      showSidebar={false}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              行情工作台在线
            </span>
            <span className="text-slate-700">|</span>
            <span>{marketDescription}</span>
          </>
        )
      }
      statusBarRight={statusBarRight}
      tabBar={
        <TabBar
          activeTabId="MARKET"
          closable={false}
          onTabChange={() => undefined}
          onTabClose={() => undefined}
          tabs={tabs}
          themeColor="blue"
        />
      }
      theme={{
        icon: BarChart3,
        name: 'blue',
        title: 'QuantX Market Studio',
      }}
    />
  );
}

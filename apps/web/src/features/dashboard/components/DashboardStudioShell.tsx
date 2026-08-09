import { LayoutDashboard } from 'lucide-react';
import { useMemo, type ReactNode } from 'react';

import {
  StudioWorkbench,
  TabBar,
  type StudioMode,
  type StudioTab,
} from '@/components/studio-workbench';

export type DashboardStudioMode = 'DASHBOARD';

const dashboardDescription = '账户、市场、快捷任务';

const dashboardModes: StudioMode[] = [
  {
    icon: LayoutDashboard,
    id: 'DASHBOARD',
    label: '仪表板',
  },
];

interface DashboardStudioShellProps {
  content: ReactNode;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
}

export function DashboardStudioShell({
  content,
  statusBarLeft,
  statusBarRight,
}: DashboardStudioShellProps) {
  const activeMode: DashboardStudioMode = 'DASHBOARD';
  const tabs = useMemo<StudioTab[]>(
    () =>
      dashboardModes.map(mode => ({
        icon: mode.icon,
        id: mode.id,
        name: mode.label,
        type: 'dashboard-resource',
      })),
    []
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      content={content}
      isPage
      modes={dashboardModes}
      onModeChange={() => undefined}
      showSidebar={false}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              仪表板在线
            </span>
            <span className="text-slate-700">|</span>
            <span>{dashboardDescription}</span>
          </>
        )
      }
      statusBarRight={statusBarRight}
      tabBar={
        <TabBar
          activeTabId="DASHBOARD"
          closable={false}
          onTabChange={() => undefined}
          onTabClose={() => undefined}
          tabs={tabs}
          themeColor="red"
        />
      }
      theme={{
        icon: LayoutDashboard,
        name: 'red',
        title: 'QuantX Overview Studio',
      }}
    />
  );
}

import {
  Activity,
  BookOpen,
  Boxes,
  GitCommitHorizontal,
  History,
  Settings,
} from 'lucide-react';
import type { ReactNode } from 'react';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';

export type StrategyStudioMode =
  | 'BACKTEST'
  | 'CATALOG'
  | 'CONFIG'
  | 'MONITOR'
  | 'RUNS'
  | 'TRACE';

export const strategyStudioModes: StudioMode[] = [
  { id: 'RUNS', icon: Boxes, label: '运行实例' },
  { id: 'CATALOG', icon: BookOpen, label: '策略库' },
  { id: 'MONITOR', icon: Activity, label: '图表监控' },
  { id: 'BACKTEST', icon: History, label: '回测版本' },
  { id: 'TRACE', icon: GitCommitHorizontal, label: '决策追踪' },
  { id: 'CONFIG', icon: Settings, label: '参数配置' },
];

interface StrategyStudioShellProps {
  activeMode: StrategyStudioMode;
  className?: string;
  content: ReactNode;
  onExit?: () => void;
  onModeChange: (mode: StrategyStudioMode) => void;
  showSidebar?: boolean;
  sidebar?: ReactNode;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
  tabBar?: ReactNode;
}

export function StrategyStudioShell({
  activeMode,
  className,
  content,
  onExit,
  onModeChange,
  showSidebar = true,
  sidebar,
  statusBarLeft,
  statusBarRight,
  tabBar,
}: StrategyStudioShellProps) {
  return (
    <StudioWorkbench
      activeMode={activeMode}
      className={className}
      content={content}
      isPage
      modes={strategyStudioModes}
      onExit={onExit}
      onModeChange={mode => onModeChange(mode as StrategyStudioMode)}
      showSidebar={showSidebar}
      sidebar={sidebar}
      sidebarSizing={{
        defaultWidth: 304,
        maxWidth: 420,
        minWidth: 248,
        storageScope: 'strategy-studio',
      }}
      statusBarLeft={statusBarLeft}
      statusBarRight={statusBarRight}
      tabBar={tabBar}
      theme={{
        icon: Activity,
        name: 'red',
        title: 'QuantX Strategy Studio',
      }}
    />
  );
}

import { Activity } from 'lucide-react';
import type { ReactNode } from 'react';

import { StudioWorkbench } from '@/components/studio-workbench';

import {
  strategyStudioModes,
  type StrategyStudioMode,
} from './strategy-studio-modes';

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
        name: 'blue',
        title: 'QuantX Strategy Studio',
      }}
    />
  );
}

import {
  Activity,
  BookOpen,
  Boxes,
  GitCommitHorizontal,
  History,
  Settings,
} from 'lucide-react';

import type { StudioMode } from '@/components/studio-workbench';

export type StrategyStudioMode =
  'BACKTEST' | 'CATALOG' | 'CONFIG' | 'MONITOR' | 'RUNS' | 'TRACE';

export const strategyStudioModes: StudioMode[] = [
  { id: 'RUNS', icon: Boxes, label: '运行实例' },
  { id: 'CATALOG', icon: BookOpen, label: '策略库' },
  { id: 'MONITOR', icon: Activity, label: '图表监控' },
  { id: 'BACKTEST', icon: History, label: '回测版本' },
  { id: 'TRACE', icon: GitCommitHorizontal, label: '决策追踪' },
  { id: 'CONFIG', icon: Settings, label: '参数配置' },
];

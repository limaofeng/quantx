import {
  ArrowLeftRight,
  BarChart3,
  Building2,
  ClipboardList,
  FileText,
  History,
  LayoutDashboard,
  Wallet,
} from 'lucide-react';

import type { StudioMode } from '@/components/studio-workbench';

export type StockWorkspaceContext = 'detail' | 'holdings';
export type StockWorkspaceView =
  | 'OVERVIEW'
  | 'CHART'
  | 'ANNOUNCEMENTS'
  | 'FINANCIAL'
  | 'TRADING'
  | 'ORDER'
  | 'ORDERS'
  | 'TRADES'
  | 'ACCOUNT';

export const holdingsWorkspaceModes: StudioMode[] = [
  { id: 'CHART', icon: BarChart3, label: '图表盘口' },
  { id: 'ORDER', icon: ArrowLeftRight, label: '下单' },
  { id: 'ORDERS', icon: ClipboardList, label: '委托' },
  { id: 'TRADES', icon: History, label: '成交' },
  { id: 'ACCOUNT', icon: Wallet, label: '账户' },
];

export const detailWorkspaceModes: StudioMode[] = [
  { id: 'OVERVIEW', icon: LayoutDashboard, label: '概览' },
  { id: 'CHART', icon: BarChart3, label: '图表' },
  { id: 'ANNOUNCEMENTS', icon: FileText, label: '公告' },
  { id: 'FINANCIAL', icon: Building2, label: '财务' },
  { id: 'TRADING', icon: ArrowLeftRight, label: '交易' },
];

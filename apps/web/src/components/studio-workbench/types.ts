import type React from 'react';

export type StudioThemeName =
  'cyan' | 'blue' | 'red' | 'amber' | 'emerald' | 'rose';

export interface StudioTheme {
  name: StudioThemeName;
  icon: React.ElementType;
  title: string;
}

export interface StudioMode {
  id: string;
  icon: React.ElementType;
  label: string;
  disabled?: boolean;
  disabledReason?: string;
}

export interface StudioAction {
  id: string;
  icon: React.ElementType;
  label: string;
  active?: boolean;
  badge?: boolean;
  onHover?: () => void;
  onSelect: () => void;
}

export interface StudioTab {
  id: string;
  type: string;
  name: string;
  icon?: React.ElementType;
  isDirty?: boolean;
  isPreview?: boolean;
  isPreviewable?: boolean;
  payload?: unknown;
}

export interface StudioSidebarSizing {
  storageScope?: string;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
}

export interface StudioWorkbenchProps {
  theme: StudioTheme;
  modes: StudioMode[];
  activeMode: string;
  onModeChange: (mode: string) => void;
  globalActions?: StudioAction[];
  sidebar?: React.ReactNode;
  showSidebar?: boolean;
  sidebarSizing?: StudioSidebarSizing;
  tabBar?: React.ReactNode;
  content: React.ReactNode;
  emptyState?: React.ReactNode;
  isEmpty?: boolean;
  statusBarLeft?: React.ReactNode;
  statusBarRight?: React.ReactNode;
  onExit?: () => void;
  utilityActions?: StudioAction[];
  isPage?: boolean;
  className?: string;
}

export interface ActivityBarProps {
  theme: StudioTheme;
  modes: StudioMode[];
  activeMode: string;
  onModeChange: (mode: string) => void;
  globalActions?: StudioAction[];
  onExit?: () => void;
  utilityActions?: StudioAction[];
}

export interface StatusBarProps {
  left?: React.ReactNode;
  right?: React.ReactNode;
}

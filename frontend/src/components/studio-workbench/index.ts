export { ActivityBar } from './ActivityBar';
export { StatusBar } from './StatusBar';
export {
  StudioStatusBarOutlet,
  StudioStatusBarProvider,
} from './StudioStatusBarProvider';
export { StudioMenu } from './StudioMenu';
export { StudioTabContextMenu } from './StudioTabContextMenu';
export { StudioWorkbench } from './StudioWorkbench';
export { TabBar } from './TabBar';
export { useStudioGlobalActions } from './useStudioGlobalActions';
export { useStudioMenu } from './useStudioMenu';
export { useStudioTabs } from './useStudioTabs';
export { usePageStudioStatusBar } from './usePageStudioStatusBar';

export type {
  ActivityBarProps,
  StudioAction,
  StatusBarProps,
  StudioMode,
  StudioSidebarSizing,
  StudioTab,
  StudioTheme,
  StudioThemeName,
  StudioWorkbenchProps,
} from './types';
export type { TabBarProps } from './TabBar';
export type {
  StudioMenuActionItem,
  StudioMenuAnchor,
  StudioMenuItem,
  StudioMenuPlacement,
  StudioMenuSeparatorItem,
  StudioMenuState,
} from './StudioMenu';
export type {
  StudioTabContextMenuAction,
  StudioTabContextMenuState,
} from './StudioTabContextMenu';
export type { UseStudioTabsResult } from './useStudioTabs';
